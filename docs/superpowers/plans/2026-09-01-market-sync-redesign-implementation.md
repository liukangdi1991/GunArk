# 行情同步机制重做（两阶段 + 原子账本）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地已定稿的 `docs/superpowers/specs/2026-08-27-market-sync-redesign-design.md`：两阶段同步（日历表 + 行情）、`build_plan` 纯函数决策、原子账本、4 条自检断言、失败分类与逃逸阀、前端面板重做，并删除旧 `syncer.py` 全部死代码。

**Architecture:** 新模块按 spec §3.1 布局：`domain/market/sync/`（纯决策与自检）→ `infrastructure/`（fetch/writer/runner/sync_store/stocklist/calendar）→ `app/services/market_sync/`（编排与提交）→ `interfaces/`（API 与面板数据）。旧 `syncer.py`/`markers.py` 在接管完成前保持可用，Task 15 统一删除。

**Tech Stack:** Python ≥3.11、Polars、SQLite(WAL)、FastAPI、React 18 + antd 5 + Vite。

**全局约定：**
- 所有命令在仓库根目录执行；测试一律 `.venv/bin/pytest`（系统 python 无 polars）。
- 前端验证：`cd frontend && npm run build`（= `tsc -b && vite build`）。
- 提交信息沿用现有风格：`feat:` / `test:` / `refactor:` / `chore:` + 中文描述。
- 基线：实施前全量测试 378 passed；每个 Task 结束后相关测试必须全绿。
- 与 spec 的两处实现细化（不改变语义）：
  1. `sync_skipped` 表在 spec DDL 基础上**增加 `kind TEXT` 列**——带缺口提交需判别"缺口含 env 类"（spec §3.7），分类必须持久化。
  2. `merge_day_bars`（旧在 syncer）不迁入 `fetch.py`，其算法由 `writer.flush_by_code` 承接（同一算法，避免双份实现）。

---

## 文件结构

```
新建：
  trendradar/domain/market/sync/__init__.py
  trendradar/domain/market/sync/spec.py        # 常量 / PlanKind / FailureKind / SyncPlan / latest_tradeable_day
  trendradar/domain/market/sync/planner.py     # build_plan 纯函数
  trendradar/domain/market/sync/selfcheck.py   # 4 条自检断言（纯函数）
  trendradar/infrastructure/storage/sync_store.py   # 账本四表读写（唯一写账本模块）
  trendradar/infrastructure/tushare/fetch.py   # 抓取原语 + FailureKind 分类
  trendradar/infrastructure/tushare/writer.py  # flush_by_code / 单向换名 / 读回
  trendradar/infrastructure/tushare/runner.py  # run_incremental / run_full / run_backfill
  trendradar/app/services/market_sync/__init__.py
  trendradar/app/services/market_sync/commit.py    # 单事务落账
  trendradar/app/services/market_sync/service.py   # stage-1/stage-2 编排
  tests/domain/test_sync_spec.py
  tests/domain/test_sync_planner.py
  tests/domain/test_sync_selfcheck.py
  tests/infrastructure/test_sync_store.py
  tests/infrastructure/test_fetch.py
  tests/infrastructure/test_stocklist.py
  tests/infrastructure/test_writer.py
  tests/infrastructure/test_runner.py
  tests/app/test_market_sync_service.py

修改：
  trendradar/infrastructure/storage/schema.py      # +4 表；最后删 market_sync_runs
  trendradar/infrastructure/tushare/stocklist.py   # L∪D + delist_date + EffectiveList
  trendradar/infrastructure/tushare/calendar.py    # 只留 fetch_trade_calendar
  trendradar/app/jobs/executor.py                  # 互斥集合化 + 终态不覆写
  trendradar/app/jobs/context.py                   # +store 只读属性
  trendradar/app/services/market_service.py        # 整体重写为新门面
  trendradar/interfaces/api/schemas/market.py      # 新请求形状
  trendradar/interfaces/api/routes/market.py       # sync/backfill/confirm-doubtful
  trendradar/interfaces/api/presenters.py          # 新 status 计算 + invalidate + jtype 改名
  frontend/src/types/marketData.ts                 # 新状态形状；删幽灵字段
  frontend/src/types/execution.ts                  # market_data_sync → market_bars_sync
  frontend/src/services/marketData.ts              # +backfill/confirmDoubtful
  frontend/src/pages/MarketData/MarketDataPage.tsx # 整体重写
  tests/infrastructure/test_schema.py、tests/app/test_executor_mutex.py、
  tests/app/test_execution_registration.py、tests/interfaces/test_api_contract.py（随各任务更新）

删除（Task 15）：
  trendradar/infrastructure/tushare/syncer.py
  trendradar/infrastructure/tushare/markers.py
  tests/infrastructure/test_markers.py、test_sync_plan.py、test_sync_planner.py、
  test_sync_by_stock.py、test_sync_market.py、test_sync_integration.py、
  test_sync_daily.py、test_tushare_syncer.py（能力已由新测试文件覆盖）
```

---

### Task 1: domain/market/sync/spec.py —— 常量与数据结构

**Files:**
- Create: `trendradar/domain/market/sync/__init__.py`
- Create: `trendradar/domain/market/sync/spec.py`
- Test: `tests/domain/test_sync_spec.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/domain/test_sync_spec.py
from datetime import date, datetime
from zoneinfo import ZoneInfo

from trendradar.domain.market.sync.spec import (
    BASELINE_START,
    DATA_CUTOFF_HOUR,
    FailureKind,
    PlanKind,
    SyncPlan,
    latest_tradeable_day,
)

CN = ZoneInfo("Asia/Shanghai")


def test_constants():
    assert BASELINE_START == date(2015, 1, 1)
    assert DATA_CUTOFF_HOUR == 16


def test_enum_values():
    assert PlanKind.BLOCKED.value == "blocked"
    assert PlanKind.REBUILD_REQUIRED.value == "rebuild_required"
    assert PlanKind.BACKFILL_CODES.value == "backfill_codes"
    assert FailureKind.ENV.value == "env"
    assert FailureKind.OK_EMPTY.value == "ok_empty"


def test_sync_plan_is_frozen():
    p = SyncPlan(kind=PlanKind.UPTODATE, latest_tradeable=None,
                 missing_days=[], stale_days=0, reason="x")
    import dataclasses
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.reason = "y"


def test_latest_tradeable_before_cutoff_is_previous_trade_day():
    days = {date(2026, 8, 26), date(2026, 8, 27)}
    now = datetime(2026, 8, 27, 10, 0, tzinfo=CN)
    assert latest_tradeable_day(days, now) == date(2026, 8, 26)


def test_latest_tradeable_at_cutoff_on_trade_day_is_today():
    days = {date(2026, 8, 26), date(2026, 8, 27)}
    now = datetime(2026, 8, 27, 16, 0, tzinfo=CN)
    assert latest_tradeable_day(days, now) == date(2026, 8, 27)


def test_latest_tradeable_weekend_is_friday():
    days = {date(2026, 8, 28), date(2026, 8, 31)}  # 周五 / 下周一
    now = datetime(2026, 8, 29, 10, 0, tzinfo=CN)  # 周六
    assert latest_tradeable_day(days, now) == date(2026, 8, 28)


def test_latest_tradeable_empty_calendar_is_none():
    assert latest_tradeable_day(set(), datetime(2026, 8, 27, 16, 0, tzinfo=CN)) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/domain/test_sync_spec.py -v`
Expected: FAIL（ModuleNotFoundError: trendradar.domain.market.sync）

- [ ] **Step 3: 实现**

```python
# trendradar/domain/market/sync/spec.py
"""同步机制重做：常量与数据结构（纯，零 I/O）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from zoneinfo import ZoneInfo

BASELINE_START = date(2015, 1, 1)
DATA_CUTOFF_HOUR = 16
SHANGHAI = ZoneInfo("Asia/Shanghai")


class PlanKind(str, Enum):
    BLOCKED = "blocked"
    REBUILD_REQUIRED = "rebuild_required"
    FULL = "full"
    INCREMENTAL = "incremental"
    UPTODATE = "uptodate"
    BACKFILL_CODES = "backfill_codes"


class FailureKind(str, Enum):
    ENV = "env"
    CODE = "code"
    UNKNOWN = "unknown"
    OK_EMPTY = "ok_empty"


@dataclass(frozen=True)
class SyncPlan:
    kind: PlanKind
    latest_tradeable: date | None
    missing_days: list[date]
    stale_days: int
    reason: str


def latest_tradeable_day(trade_days: set[date], now_cn: datetime) -> date | None:
    """最近一个数据已可得（北京 16:00 截止）的交易日。

    语义沿用旧 syncer.latest_tradeable_day；日历为空时返回 None
    （旧实现返回 today 的分支由 planner 的 BLOCKED 行承接）。
    """
    if not trade_days:
        return None
    today = now_cn.date()
    if now_cn.hour >= DATA_CUTOFF_HOUR and today in trade_days:
        return today
    past = sorted(d for d in trade_days if d < today)
    return past[-1] if past else None
```

`trendradar/domain/market/sync/__init__.py` 为空文件。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/domain/test_sync_spec.py -v`
Expected: PASS（7 个）

- [ ] **Step 5: 提交**

```bash
git add trendradar/domain/market/sync/__init__.py trendradar/domain/market/sync/spec.py tests/domain/test_sync_spec.py
git commit -m "feat: 同步重做 domain 层 spec 常量与数据结构"
```

---

### Task 2: domain/market/sync/planner.py —— build_plan 决策矩阵（R4）

**Files:**
- Create: `trendradar/domain/market/sync/planner.py`
- Test: `tests/domain/test_sync_planner.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/domain/test_sync_planner.py
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from trendradar.domain.market.sync.planner import build_plan
from trendradar.domain.market.sync.spec import PlanKind

CN = ZoneInfo("Asia/Shanghai")
TODAY = date(2026, 8, 27)
NOW = datetime(2026, 8, 27, 18, 0, tzinfo=CN)  # 16:00 后，今日可得
CAL = {date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27), date(2026, 12, 31)}
DONE_FULL = {date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27)}


def test_row1_blocked_calendar_stale():
    plan = build_plan(date(2027, 1, 2), NOW, {date(2026, 12, 31)}, DONE_FULL, False, {})
    assert plan.kind is PlanKind.BLOCKED
    assert plan.latest_tradeable is None


def test_row1_blocked_calendar_empty():
    plan = build_plan(TODAY, NOW, set(), DONE_FULL, False, {})
    assert plan.kind is PlanKind.BLOCKED


def test_row2_codes_backfill():
    plan = build_plan(TODAY, NOW, CAL, DONE_FULL, False, {"codes": ["000001"]})
    assert plan.kind is PlanKind.BACKFILL_CODES


def test_row3_force_full_even_when_suspect():
    plan = build_plan(TODAY, NOW, CAL, DONE_FULL, True, {"force": True})
    assert plan.kind is PlanKind.FULL


def test_row4_first_build_full():
    plan = build_plan(TODAY, NOW, CAL, set(), False, {})
    assert plan.kind is PlanKind.FULL
    assert plan.reason == "首次建库"


def test_row5_suspect_rebuild_required_not_full():
    plan = build_plan(TODAY, NOW, CAL, DONE_FULL, True, {})
    assert plan.kind is PlanKind.REBUILD_REQUIRED


def test_row6_uptodate():
    plan = build_plan(TODAY, NOW, CAL, DONE_FULL, False, {})
    assert plan.kind is PlanKind.UPTODATE
    assert plan.missing_days == []
    assert plan.stale_days == 0


def test_row7_incremental_tail_gap():
    done = {date(2026, 8, 25)}
    plan = build_plan(TODAY, NOW, CAL, done, False, {})
    assert plan.kind is PlanKind.INCREMENTAL
    assert plan.missing_days == [date(2026, 8, 26), date(2026, 8, 27)]
    assert plan.stale_days == 2
    assert plan.latest_tradeable == date(2026, 8, 27)


def test_mid_hole_missing_greater_than_stale():
    # 日历 25/26/27；done 只有 25 与 27：中段洞 26 + 尾部 0
    done = {date(2026, 8, 25), date(2026, 8, 27)}
    plan = build_plan(TODAY, NOW, CAL, done, False, {})
    assert plan.kind is PlanKind.INCREMENTAL
    assert plan.missing_days == [date(2026, 8, 26)]
    assert plan.stale_days == 0
    assert len(plan.missing_days) > plan.stale_days


def test_stale_days_counts_consecutive_tail():
    cal = {date(2026, 8, 20), date(2026, 8, 21), date(2026, 8, 24),
           date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27),
           date(2026, 12, 31)}
    done = {date(2026, 8, 20), date(2026, 8, 24)}
    plan = build_plan(TODAY, NOW, cal, done, False, {})
    assert plan.missing_days == [date(2026, 8, 21), date(2026, 8, 25),
                                 date(2026, 8, 26), date(2026, 8, 27)]
    assert plan.stale_days == 3  # 27/26/25 尾部连续，止于 24


def test_matrix_priority_codes_beats_force():
    plan = build_plan(TODAY, NOW, CAL, set(), False, {"codes": ["000001"], "force": True})
    assert plan.kind is PlanKind.BACKFILL_CODES
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/domain/test_sync_planner.py -v`
Expected: FAIL（ImportError: planner）

- [ ] **Step 3: 实现**

```python
# trendradar/domain/market/sync/planner.py
"""build_plan：stage-2 唯一决策出口（纯函数，零 I/O，零 now()）。"""

from __future__ import annotations

from datetime import date, datetime

from trendradar.domain.market.sync.spec import (
    BASELINE_START,
    PlanKind,
    SyncPlan,
    latest_tradeable_day,
)


def stale_days(calendar_days: set[date], done_days: set[date], latest: date | None) -> int:
    """从 latest 沿官方日历反向回溯到第一个已入账日的交易日数。
    公开导出：presenters 的新鲜度面板复用同一口径（spec §4）。"""
    if latest is None:
        return 0
    ordered = sorted(d for d in calendar_days if d <= latest)
    count = 0
    for d in reversed(ordered):
        if d in done_days:
            break
        count += 1
    return count


def build_plan(
    today_cn: date,
    now_cn: datetime,
    calendar_days: set[date],
    done_days: set[date],
    ledger_suspect: bool,
    request: dict,
) -> SyncPlan:
    """决策矩阵（自上而下，首个匹配生效），见 spec §3.4。"""
    if not calendar_days or max(calendar_days) < today_cn:
        return SyncPlan(PlanKind.BLOCKED, None, [], 0,
                        "官方日历未就绪/过期，拒绝判断新鲜度")

    latest = latest_tradeable_day(calendar_days, now_cn)
    missing = sorted(
        d for d in calendar_days
        if latest is not None and BASELINE_START <= d <= latest and d not in done_days
    )
    stale = stale_days(calendar_days, done_days, latest)

    if request.get("codes"):
        return SyncPlan(PlanKind.BACKFILL_CODES, latest, missing, stale,
                        "指定代码补齐（不参与日账本）")
    if request.get("force"):
        return SyncPlan(PlanKind.FULL, latest, missing, stale,
                        "用户显式要求全量重建")
    if not done_days:
        return SyncPlan(PlanKind.FULL, latest, missing, stale, "首次建库")
    if ledger_suspect:
        return SyncPlan(PlanKind.REBUILD_REQUIRED, latest, missing, stale,
                        "账本曾被自检判为不可信，需人工确认重建")
    if not missing:
        return SyncPlan(PlanKind.UPTODATE, latest, [], 0, "已覆盖至最近可交易日")
    return SyncPlan(PlanKind.INCREMENTAL, latest, missing, stale,
                    f"待补 {len(missing)} 个交易日")
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/domain/test_sync_planner.py -v`
Expected: PASS（11 个）

- [ ] **Step 5: 提交**

```bash
git add trendradar/domain/market/sync/planner.py tests/domain/test_sync_planner.py
git commit -m "feat: build_plan 决策矩阵纯函数（spec §3.4，R4）"
```

---

### Task 3: domain/market/sync/selfcheck.py —— 4 条自检断言

**Files:**
- Create: `trendradar/domain/market/sync/selfcheck.py`
- Test: `tests/domain/test_sync_selfcheck.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/domain/test_sync_selfcheck.py
from datetime import date

import polars as pl

from trendradar.domain.market.sync.selfcheck import (
    ROW_COUNT_RATIO,
    coverage_ok,
    doubtful_by_row_count,
    expected_trading_count,
    file_structure_ok,
    ledger_subset_ok,
)

EFFECTIVE = [
    (date(2010, 1, 1), None),                    # 在市
    (date(2010, 1, 1), date(2016, 6, 30)),       # 退市
    (date(2020, 3, 1), None),                    # 晚上市
]


def test_expected_trading_count():
    assert expected_trading_count(EFFECTIVE, date(2015, 6, 1)) == 2
    assert expected_trading_count(EFFECTIVE, date(2017, 1, 1)) == 1  # 退市股已出
    assert expected_trading_count(EFFECTIVE, date(2016, 6, 30)) == 2  # 退市当日仍计
    assert expected_trading_count(EFFECTIVE, date(2021, 1, 1)) == 2


def test_doubtful_by_row_count_075_threshold():
    eff = [(date(2010, 1, 1), None)] * 100  # expected = 100
    assert doubtful_by_row_count({date(2015, 6, 1): 75}, eff) == []  # 恰 0.75 通过
    assert doubtful_by_row_count({date(2015, 6, 1): 74}, eff) == [date(2015, 6, 1)]
    # 2015 实测场景 2335/2733 = 0.854 应通过
    eff2 = [(date(2010, 1, 1), None)] * 2733
    assert doubtful_by_row_count({date(2015, 6, 1): 2335}, eff2) == []
    # 半截响应 ~0.5 应报警
    assert doubtful_by_row_count({date(2015, 6, 1): 1366}, eff2) == [date(2015, 6, 1)]


def test_coverage_ok():
    assert coverage_ok({date(2026, 8, 26), date(2026, 8, 27)}, {date(2026, 8, 27)})
    assert not coverage_ok({date(2026, 8, 26)}, {date(2026, 8, 27)})


def _bars_df(rows):
    return pl.DataFrame(rows)


def test_file_structure_ok_valid():
    df = _bars_df([
        {"date": date(2026, 8, 26), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
        {"date": date(2026, 8, 27), "open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0},
    ])
    assert file_structure_ok(df)


def test_file_structure_ok_rejects_duplicate_dates():
    df = _bars_df([
        {"date": date(2026, 8, 26), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
        {"date": date(2026, 8, 26), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
    ])
    assert not file_structure_ok(df)


def test_file_structure_ok_rejects_descending_dates():
    df = _bars_df([
        {"date": date(2026, 8, 27), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
        {"date": date(2026, 8, 26), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
    ])
    assert not file_structure_ok(df)


def test_file_structure_ok_rejects_nan_close():
    df = _bars_df([
        {"date": date(2026, 8, 26), "open": 1.0, "high": 2.0, "low": 0.5, "close": float("nan")},
    ])
    assert not file_structure_ok(df)


def test_file_structure_ok_empty_is_ok():
    assert file_structure_ok(pl.DataFrame())


def test_ledger_subset_ok():
    assert ledger_subset_ok({date(2026, 8, 27)}, {date(2026, 8, 26), date(2026, 8, 27)})
    assert not ledger_subset_ok({date(2026, 8, 28)}, {date(2026, 8, 27)})


def test_ratio_constant():
    assert ROW_COUNT_RATIO == 0.75
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/domain/test_sync_selfcheck.py -v`
Expected: FAIL（ImportError: selfcheck）

- [ ] **Step 3: 实现**

```python
# trendradar/domain/market/sync/selfcheck.py
"""4 条自检断言（纯函数，输入内存副本 + 集合）。见 spec §3.8。"""

from __future__ import annotations

from datetime import date

import polars as pl

ROW_COUNT_RATIO = 0.75

_EFFECTIVE_ROW = tuple[date, date | None]  # (list_date, delist_date)


def expected_trading_count(effective: list[_EFFECTIVE_ROW], day: date) -> int:
    """expected(d)：有效清单中 d 日应市的股票数。"""
    return sum(
        1 for list_d, delist_d in effective
        if list_d <= day and (delist_d is None or delist_d >= day)
    )


def doubtful_by_row_count(
    day_rows: dict[date, int],
    effective: list[_EFFECTIVE_ROW],
    threshold: float = ROW_COUNT_RATIO,
) -> list[date]:
    """断言①：行数 < threshold × expected(d) 的日期（升序）。"""
    return sorted(
        d for d, n in day_rows.items()
        if n < threshold * expected_trading_count(effective, d)
    )


def coverage_ok(calendar: set[date], claimed: set[date]) -> bool:
    """断言②：读回实测日历 ⊇ 声称日集合。"""
    return claimed <= calendar


def file_structure_ok(df: pl.DataFrame) -> bool:
    """断言③：日期严格递增（含无重复）+ OHLC 无 NaN。"""
    if df.is_empty():
        return True
    dates = df["date"].to_list()
    if any(a >= b for a, b in zip(dates, dates[1:])):
        return False
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            return False
        if df[col].null_count() > 0 or bool(df[col].is_nan().any()):
            return False
    return True


def ledger_subset_ok(new_done: set[date], calendar: set[date]) -> bool:
    """断言④：新增入账日 ⊆ 读回实测日历。"""
    return new_done <= calendar
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/domain/test_sync_selfcheck.py -v`
Expected: PASS（10 个）

- [ ] **Step 5: 提交**

```bash
git add trendradar/domain/market/sync/selfcheck.py tests/domain/test_sync_selfcheck.py
git commit -m "feat: 自检四断言纯函数（行数/读回/结构/账本子集）"
```

---

### Task 4: schema.py —— 新增账本四表

**Files:**
- Modify: `trendradar/infrastructure/storage/schema.py`
- Test: `tests/infrastructure/test_schema.py`

注意：`market_sync_runs` DDL 本任务**保留**（其写入方/读取方要到 Task 13/15 才删；顺序要求见 spec §5）。

- [ ] **Step 1: 更新测试（先失败）**

`tests/infrastructure/test_schema.py` 的 `test_schema_creates_all_tables` 中 `expected` 集合追加 4 个表名（`market_sync_runs` 暂时保留）：

```python
    expected = {
        "artifacts",
        "execution_items",
        "execution_links",
        "executions",
        "job_logs",
        "jobs",
        "market_sync_runs",
        "strategy_group_members",
        "strategy_groups",
        "strategy_settings",
        "sync_done_days",
        "sync_meta",
        "sync_skipped",
        "trade_calendar",
    }
```

并在文件末尾追加：

```python
def test_calendar_insert_or_ignore(tmp_path):
    sc = StorageConnection(tmp_path)
    conn = sc.connect()
    init_schema(conn)
    conn.execute("INSERT OR IGNORE INTO trade_calendar (trade_date) VALUES ('2026-08-27')")
    conn.execute("INSERT OR IGNORE INTO trade_calendar (trade_date) VALUES ('2026-08-27')")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) AS c FROM trade_calendar").fetchone()["c"] == 1


def test_sync_skipped_upsert_conflict(tmp_path):
    sc = StorageConnection(tmp_path)
    conn = sc.connect()
    init_schema(conn)
    conn.execute(
        "INSERT INTO sync_skipped (code, attempts, last_error, first_seen, last_attempt, kind) "
        "VALUES ('000001', 1, 'e1', '2026-08-27', '2026-08-27', 'code')"
    )
    conn.execute(
        "INSERT INTO sync_skipped (code, attempts, last_error, first_seen, last_attempt, kind) "
        "VALUES ('000001', 1, 'e2', '2026-08-28', '2026-08-28', 'code') "
        "ON CONFLICT(code) DO UPDATE SET attempts = attempts + 1, "
        "last_error = excluded.last_error, last_attempt = excluded.last_attempt"
    )
    conn.commit()
    row = conn.execute("SELECT * FROM sync_skipped WHERE code = '000001'").fetchone()
    assert row["attempts"] == 2
    assert row["last_error"] == "e2"
    assert row["first_seen"] == "2026-08-27"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/infrastructure/test_schema.py -v`
Expected: FAIL（集合不等 + 表不存在）

- [ ] **Step 3: 实现 —— 在 `schema.py` 的 DDL 中 `market_sync_runs` 表之后追加**

```sql
CREATE TABLE IF NOT EXISTS trade_calendar (
    trade_date TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS sync_done_days (
    trade_date TEXT PRIMARY KEY,
    synced_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_skipped (
    code         TEXT PRIMARY KEY,
    attempts     INTEGER NOT NULL DEFAULT 1,
    last_error   TEXT,
    first_seen   TEXT NOT NULL,
    last_attempt TEXT NOT NULL,
    kind         TEXT
);
```

（`sync_skipped.kind` 为实现细化：带缺口提交需判别缺口是否含 `env` 类，见计划头部说明。）

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/infrastructure/test_schema.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add trendradar/infrastructure/storage/schema.py tests/infrastructure/test_schema.py
git commit -m "feat: 账本四表 DDL（trade_calendar/sync_done_days/sync_meta/sync_skipped）"
```

---

### Task 5: infrastructure/storage/sync_store.py —— 账本读写（唯一写账本模块）

**Files:**
- Create: `trendradar/infrastructure/storage/sync_store.py`
- Test: `tests/infrastructure/test_sync_store.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/infrastructure/test_sync_store.py
from datetime import date

import pytest

from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema
from trendradar.infrastructure.storage.sync_store import SyncStore


@pytest.fixture()
def store(tmp_path):
    sc = StorageConnection(tmp_path)
    init_schema(sc.connect())
    return SyncStore(tmp_path)


def test_calendar_insert_and_read(store):
    store.insert_calendar_days([date(2026, 8, 26), date(2026, 8, 27)])
    store.insert_calendar_days([date(2026, 8, 27), date(2026, 8, 28)])  # 幂等
    assert store.calendar_days() == {date(2026, 8, 26), date(2026, 8, 27), date(2026, 8, 28)}
    assert store.max_calendar_day() == date(2026, 8, 28)


def test_calendar_empty(store):
    assert store.calendar_days() == set()
    assert store.max_calendar_day() is None


def test_done_days_add_and_replace(store):
    store.add_done_days([date(2026, 8, 26), date(2026, 8, 27)])
    assert store.done_days() == {date(2026, 8, 26), date(2026, 8, 27)}
    store.replace_done_days({date(2026, 8, 27)})
    assert store.done_days() == {date(2026, 8, 27)}


def test_meta_ledger_suspect_default_false(store):
    assert store.ledger_suspect() is False
    store.set_ledger_suspect(True)
    assert store.ledger_suspect() is True
    store.set_ledger_suspect(False)
    assert store.ledger_suspect() is False


def test_meta_doubtful_days_roundtrip(store):
    assert store.doubtful_days() == []
    store.set_doubtful_days([date(2015, 7, 8), date(2015, 7, 9)])
    assert store.doubtful_days() == [date(2015, 7, 8), date(2015, 7, 9)]
    store.set_doubtful_days([])
    assert store.doubtful_days() == []


def test_last_full_success_at(store):
    assert store.get_meta("last_full_success_at") is None
    store.set_last_full_success()
    assert store.get_meta("last_full_success_at") is not None


def test_skipped_record_and_excluded(store):
    assert store.skipped_rows() == []
    assert store.excluded_codes() == []
    store.record_skip_failure("000001", "参数错误", "code")
    store.record_skip_failure("000001", "参数错误2", "code")
    store.record_skip_failure("000001", "参数错误3", "code")
    rows = store.skipped_rows()
    assert len(rows) == 1
    assert rows[0]["attempts"] == 3
    assert rows[0]["last_error"] == "参数错误3"
    assert store.excluded_codes() == ["000001"]  # attempts >= 3 才出列
    store.record_skip_failure("000002", "x", "unknown")
    assert store.excluded_codes() == ["000001"]  # attempts=1 不出列


def test_skipped_success_clears(store):
    store.record_skip_failure("000001", "e", "code")
    store.clear_skip("000001")
    assert store.skipped_rows() == []


def test_skipped_reset_attempts(store):
    store.record_skip_failure("000001", "e", "code")
    store.record_skip_failure("000001", "e", "code")
    store.record_skip_failure("000001", "e", "code")
    store.reset_skip_attempts()
    rows = store.skipped_rows()
    assert rows[0]["attempts"] == 0
    assert store.excluded_codes() == []


def test_transaction_rollback_on_error(store):
    with pytest.raises(RuntimeError):
        with store.transaction() as conn:
            conn.execute(
                "INSERT INTO sync_done_days (trade_date, synced_at) VALUES ('2026-08-26', 'x')"
            )
            raise RuntimeError("boom")
    assert store.done_days() == set()


def test_transaction_commits_on_success(store):
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO sync_done_days (trade_date, synced_at) VALUES ('2026-08-26', 'x')"
        )
    assert store.done_days() == {date(2026, 8, 26)}
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/infrastructure/test_sync_store.py -v`
Expected: FAIL（ImportError: sync_store）

- [ ] **Step 3: 实现**

```python
# trendradar/infrastructure/storage/sync_store.py
"""账本四表读写 + 单事务提交（唯一有权写账本的模块）。见 spec §3.1/§5。"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from trendradar.infrastructure.storage.connection import StorageConnection


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class SyncStore:
    def __init__(self, storage_root: Path) -> None:
        self._sc = StorageConnection(Path(storage_root))

    @contextmanager
    def transaction(self):
        with self._sc.connection() as conn:
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # ---- trade_calendar（INV-2：只增不减）----

    def insert_calendar_days(self, days) -> None:
        rows = [(d.isoformat(),) for d in sorted(set(days))]
        if not rows:
            return
        with self._sc.connection() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO trade_calendar (trade_date) VALUES (?)", rows
            )
            conn.commit()

    def calendar_days(self) -> set[date]:
        with self._sc.connection() as conn:
            rows = conn.execute("SELECT trade_date FROM trade_calendar").fetchall()
        return {date.fromisoformat(r["trade_date"]) for r in rows}

    def max_calendar_day(self) -> date | None:
        with self._sc.connection() as conn:
            row = conn.execute("SELECT MAX(trade_date) AS m FROM trade_calendar").fetchone()
        return date.fromisoformat(row["m"]) if row and row["m"] else None

    # ---- sync_done_days ----

    def done_days(self) -> set[date]:
        with self._sc.connection() as conn:
            rows = conn.execute("SELECT trade_date FROM sync_done_days").fetchall()
        return {date.fromisoformat(r["trade_date"]) for r in rows}

    def add_done_days(self, days) -> None:
        rows = [(d.isoformat(), _now_iso()) for d in sorted(set(days))]
        if not rows:
            return
        with self._sc.connection() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO sync_done_days (trade_date, synced_at) VALUES (?, ?)",
                rows,
            )
            conn.commit()

    def replace_done_days(self, days: set[date]) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM sync_done_days")
            conn.executemany(
                "INSERT INTO sync_done_days (trade_date, synced_at) VALUES (?, ?)",
                [(d.isoformat(), _now_iso()) for d in sorted(days)],
            )

    # ---- sync_meta ----

    def get_meta(self, key: str) -> str | None:
        with self._sc.connection() as conn:
            row = conn.execute(
                "SELECT value FROM sync_meta WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._sc.connection() as conn:
            conn.execute(
                "INSERT INTO sync_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            conn.commit()

    def ledger_suspect(self) -> bool:
        return self.get_meta("ledger_suspect") == "1"

    def set_ledger_suspect(self, flag: bool) -> None:
        self.set_meta("ledger_suspect", "1" if flag else "0")

    def doubtful_days(self) -> list[date]:
        raw = self.get_meta("doubtful_days")
        if not raw:
            return []
        return [date.fromisoformat(d) for d in json.loads(raw)]

    def set_doubtful_days(self, days) -> None:
        self.set_meta(
            "doubtful_days", json.dumps(sorted(d.isoformat() for d in days))
        )

    def set_last_full_success(self) -> None:
        self.set_meta("last_full_success_at", _now_iso())

    # ---- sync_skipped ----

    def skipped_rows(self) -> list[dict]:
        with self._sc.connection() as conn:
            rows = conn.execute(
                "SELECT code, attempts, last_error, first_seen, last_attempt, kind "
                "FROM sync_skipped ORDER BY code"
            ).fetchall()
        return [dict(r) for r in rows]

    def excluded_codes(self) -> list[str]:
        with self._sc.connection() as conn:
            rows = conn.execute(
                "SELECT code FROM sync_skipped WHERE attempts >= 3 ORDER BY code"
            ).fetchall()
        return [r["code"] for r in rows]

    def record_skip_failure(self, code: str, error: str | None, kind: str) -> None:
        now = _now_iso()
        with self._sc.connection() as conn:
            conn.execute(
                "INSERT INTO sync_skipped (code, attempts, last_error, first_seen, last_attempt, kind) "
                "VALUES (?, 1, ?, ?, ?, ?) "
                "ON CONFLICT(code) DO UPDATE SET attempts = attempts + 1, "
                "last_error = excluded.last_error, last_attempt = excluded.last_attempt, "
                "kind = excluded.kind",
                (code, error, now, now, kind),
            )
            conn.commit()

    def clear_skip(self, code: str) -> None:
        with self._sc.connection() as conn:
            conn.execute("DELETE FROM sync_skipped WHERE code = ?", (code,))
            conn.commit()

    def reset_skip_attempts(self) -> None:
        with self._sc.connection() as conn:
            conn.execute("UPDATE sync_skipped SET attempts = 0")
            conn.commit()
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/infrastructure/test_sync_store.py -v`
Expected: PASS（11 个）

- [ ] **Step 5: 提交**

```bash
git add trendradar/infrastructure/storage/sync_store.py tests/infrastructure/test_sync_store.py
git commit -m "feat: SyncStore 账本四表读写与单事务提交"
```

---

### Task 6: infrastructure/tushare/fetch.py —— 抓取原语 + FailureKind 分类

**Files:**
- Create: `trendradar/infrastructure/tushare/fetch.py`
- Test: `tests/infrastructure/test_fetch.py`

自 `syncer.py` 原样迁移：`_fetch_with_retry`（改为返回 `FetchResult`）、`_response_to_df`、`_attach_adj_factor`、`_align_columns`、`shard_ranges`、`_to_ts_code`、exclude 过滤、`_fetch_daily_by_date`（拆分 + 分类）。`_atomic_write_parquet` 迁往 Task 8 的 `writer.py`（本任务临时内联私有副本会重复——因此 **fetch 不迁移原子写**，`merge_day_bars` 由 `writer.flush_by_code` 承接）。

- [ ] **Step 1: 写失败测试**

```python
# tests/infrastructure/test_fetch.py
from datetime import date

import pandas as pd
import polars as pl

from trendradar.domain.market.sync.spec import FailureKind
from trendradar.infrastructure.tushare.fetch import (
    classify_error,
    fetch_code_range,
    fetch_day_by_date,
    shard_ranges,
    _response_to_df,
    _to_ts_code,
    filter_excluded_boards,
)


def test_to_ts_code():
    assert _to_ts_code("600519") == "600519.SH"
    assert _to_ts_code("000001") == "000001.SZ"
    assert _to_ts_code("920099") == "920099.BJ"
    assert _to_ts_code("688001") == "688001.SH"
    assert _to_ts_code("600519.SH") == "600519.SH"  # 幂等，不产生 .SH.SH


def test_response_to_df_empty_and_rename():
    assert _response_to_df(pd.DataFrame(), "000001").is_empty()
    df = _response_to_df(pd.DataFrame([{
        "ts_code": "000001.SZ", "trade_date": "20240105",
        "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5,
        "vol": 100000, "amount": 1000000,
    }]), "000001")
    assert df["date"][0] == date(2024, 1, 5)
    assert df["volume"][0] == 100000.0
    assert df["code"][0] == "000001"
    assert df["adj_factor"][0] == 1.0


def test_shard_ranges_single_and_split():
    assert shard_ranges(date(2026, 1, 1), date(2026, 3, 1)) == [(date(2026, 1, 1), date(2026, 3, 1))]
    # 12 年 ≈ 3,130 交易日行 ≤ 5,500 → 单片（与旧实现一致）
    assert len(shard_ranges(date(2015, 1, 1), date(2026, 12, 31))) == 1
    segs = shard_ranges(date(1990, 1, 1), date(2026, 12, 31))
    assert len(segs) > 1
    assert segs[0][0] == date(1990, 1, 1)
    assert segs[-1][1] == date(2026, 12, 31)
    for (_, e), (s, _) in zip(segs, segs[1:]):
        assert e < s  # 无缝不重叠


def test_classify_error():
    assert classify_error("访问接口(daily)频率超限(300次/分钟)") is FailureKind.ENV
    assert classify_error("每分钟最多访问该接口") is FailureKind.ENV
    assert classify_error("每天最多访问该接口10000次") is FailureKind.ENV
    assert classify_error("Connection aborted") is FailureKind.ENV
    assert classify_error("Read timed out") is FailureKind.ENV
    assert classify_error("参数错误") is FailureKind.CODE
    assert classify_error("无此股票") is FailureKind.CODE
    assert classify_error(" totally weird ") is FailureKind.UNKNOWN


class FakeAdj:
    def to_dict(self, orient):
        return {}


class RateLimitPro:
    def daily(self, **kwargs):
        raise RuntimeError("访问接口(daily)频率超限(300次/分钟)")


class FlakyPro:
    def __init__(self, fails: int):
        self.fails = fails
        self.calls = 0

    def daily(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fails:
            raise RuntimeError("Connection aborted")
        return pd.DataFrame([{
            "ts_code": "000001.SZ", "trade_date": "20240105",
            "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5,
            "vol": 100000, "amount": 1000000,
        }])

    def adj_factor(self, **kwargs):
        return FakeAdj()


def test_fetch_code_range_rate_limit_is_env_no_retry():
    pro = RateLimitPro()
    result = fetch_code_range(pro, "000001", date(2024, 1, 1), date(2024, 1, 31), max_retries=3)
    assert result.kind is FailureKind.ENV
    assert result.df is None
    assert "频率超限" in result.error


def test_fetch_code_range_retries_transient_and_succeeds(monkeypatch):
    import trendradar.infrastructure.tushare.fetch as fetch

    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    pro = FlakyPro(fails=2)
    result = fetch_code_range(pro, "000001", date(2024, 1, 5), date(2024, 1, 5), max_retries=3)
    assert result.kind is None
    assert result.df is not None and result.df.height == 1
    assert pro.calls == 3


def test_fetch_code_range_bucket_timeout_is_env():
    class NoTokenBucket:
        def acquire(self, timeout=60.0, cancel_check=None):
            return False

    pro = FlakyPro(fails=0)
    result = fetch_code_range(pro, "000001", date(2024, 1, 5), date(2024, 1, 5),
                              bucket=NoTokenBucket())
    assert result.kind is FailureKind.ENV


def test_fetch_day_by_date_success_and_shape():
    class DayPro:
        def __init__(self):
            self.daily_kwargs = None

        def daily(self, **kwargs):
            self.daily_kwargs = kwargs
            return pd.DataFrame([{
                "ts_code": "000001.SZ", "trade_date": "20260827",
                "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5,
                "vol": 100000, "amount": 1000000,
            }])

        def adj_factor(self, **kwargs):
            return FakeAdj()

    pro = DayPro()
    result = fetch_day_by_date(pro, date(2026, 8, 27))
    assert result.kind is None
    assert pro.daily_kwargs == {"trade_date": "20260827"}
    assert result.df["code"][0] == "000001"
    assert result.df["date"][0] == date(2026, 8, 27)


def test_fetch_day_by_date_none_response_is_unknown():
    class NonePro:
        def daily(self, **kwargs):
            return None

    result = fetch_day_by_date(NonePro(), date(2026, 8, 27), max_retries=1)
    assert result.kind is FailureKind.UNKNOWN


def test_filter_excluded_boards_gem_star():
    df = pl.DataFrame({"code": ["300001", "688001", "600519", "000001"]})
    out = filter_excluded_boards(df, ["gem", "star"])
    assert sorted(out["code"].to_list()) == ["000001", "600519"]
    assert filter_excluded_boards(df, None).height == 4
    assert filter_excluded_boards(df, []).height == 4
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/infrastructure/test_fetch.py -v`
Expected: FAIL（ImportError: fetch）

- [ ] **Step 3: 实现**

```python
# trendradar/infrastructure/tushare/fetch.py
"""Tushare 抓取原语（自旧 syncer.py 迁移）+ 失败分类。见 spec §3.1/§3.7。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta

import polars as pl

from trendradar.domain.market.sync.spec import FailureKind

logger = logging.getLogger(__name__)

IP_BAN_ERROR_MSG = "每分钟最多访问该接口"
RATE_LIMIT_MSG = "频率超限"

ENV_MARKERS = (
    RATE_LIMIT_MSG,
    "最多访问该接口",
    "Connection",
    "Timeout",
    "timed out",
    "NewConnectionError",
    "502",
    "503",
    "504",
)
CODE_MARKERS = ("参数错误", "无此股票", "invalid parameter", "not found")

# 北交所改由 .BJ 后缀识别（stocklist.py），此处仅保留 gem/star
EXCLUDE_BOARD_PREFIXES = {"gem": ("300", "301"), "star": ("688", "689")}


@dataclass(frozen=True)
class FetchResult:
    df: pl.DataFrame | None
    kind: FailureKind | None  # None = 成功且有数据
    error: str | None = None


def classify_error(msg: str) -> FailureKind:
    for m in ENV_MARKERS:
        if m in msg:
            return FailureKind.ENV
    for m in CODE_MARKERS:
        if m in msg:
            return FailureKind.CODE
    return FailureKind.UNKNOWN


def _to_ts_code(code: str) -> str:
    # 与旧实现一致：先去后缀再补零，避免 "600519.SH" → "600519.SH.SH"
    code = str(code).split(".")[0].zfill(6)
    if code.startswith(("60", "68", "900", "901")):
        return f"{code}.SH"
    elif code.startswith(("92", "4", "8")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _response_to_df(resp, code: str) -> pl.DataFrame:
    if resp is None or not resp.to_dict(orient="list"):
        return pl.DataFrame()

    df = pl.DataFrame(resp.to_dict(orient="list"))

    column_map = {"trade_date": "date", "vol": "volume"}
    df = df.rename({k: v for k, v in column_map.items() if k in df.columns})

    if "date" in df.columns:
        df = df.with_columns(
            pl.col("date").cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d")
        )

    for col_name in ["open", "high", "low", "close", "volume", "amount", "pre_close"]:
        if col_name in df.columns:
            df = df.with_columns(pl.col(col_name).cast(pl.Float64))

    df = df.with_columns(
        pl.lit(code).cast(pl.Utf8).alias("code"),
        pl.lit(1.0).cast(pl.Float64).alias("adj_factor"),
        pl.lit(False).cast(pl.Boolean).alias("is_suspended"),
    )

    cols = ["code", "date", "open", "high", "low", "close", "volume",
            "amount", "pre_close", "adj_factor", "is_suspended"]
    df = df.select([c for c in cols if c in df.columns])
    return df.sort("date")


def _attach_adj_factor(df: pl.DataFrame, adj_df) -> pl.DataFrame:
    """按 (code, date) 合并真实复权因子，覆盖占位 1.0（语义与旧实现一致）。"""
    if df.is_empty() or adj_df is None:
        return df
    if isinstance(adj_df, pl.DataFrame):
        adj = adj_df
        if adj.is_empty() or "adj_factor" not in adj.columns:
            return df
    else:
        if not adj_df.to_dict(orient="list"):
            return df
        adj = pl.DataFrame(adj_df.to_dict(orient="list"))
    if "trade_date" in adj.columns:
        adj = adj.with_columns(
            pl.col("trade_date").cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d").alias("date")
        )
    if "ts_code" in adj.columns:
        adj = adj.with_columns(pl.col("ts_code").str.slice(0, 6).alias("code"))
    adj = adj.select(["code", "date", "adj_factor"]).rename({"adj_factor": "_adj"})
    df = df.join(adj, on=["code", "date"], how="left")
    return df.with_columns(
        pl.coalesce([pl.col("_adj"), pl.col("adj_factor")]).alias("adj_factor")
    ).drop("_adj")


def _align_columns(local: pl.DataFrame, incoming: pl.DataFrame) -> pl.DataFrame:
    """合并前把 local 对齐到 incoming 的列集与列序（语义与旧实现一致）。"""
    for col in incoming.columns:
        if col not in local.columns:
            local = local.with_columns(
                pl.lit(None).cast(incoming.schema[col]).alias(col)
            )
    return local.select(incoming.columns)


def shard_ranges(start: date, end: date, max_rows: int = 5500) -> list:
    """Split [start, end] into date ranges each estimated under max_rows."""
    total_days = (end - start).days + 1
    est_trade_days = int(total_days / 7 * 5)
    if est_trade_days <= max_rows:
        return [(start, end)]
    shards = -(-est_trade_days // max_rows)
    span = -(-total_days // shards)
    ranges = []
    cur = start
    while cur <= end:
        seg_end = min(end, cur + timedelta(days=span - 1))
        ranges.append((cur, seg_end))
        cur = seg_end + timedelta(days=1)
    return ranges


def _exclude_prefixes(exclude_boards) -> tuple | None:
    if not exclude_boards:
        return None
    prefixes = []
    for b in exclude_boards:
        prefixes.extend(EXCLUDE_BOARD_PREFIXES.get(b, ()))
    return tuple(prefixes) or None


def filter_excluded_boards(df: pl.DataFrame, exclude_boards) -> pl.DataFrame:
    prefixes = _exclude_prefixes(exclude_boards)
    if prefixes is None or df.is_empty():
        return df
    expr = ~pl.col("code").str.starts_with(prefixes[0])
    for p in prefixes[1:]:
        expr = expr & ~pl.col("code").str.starts_with(p)
    return df.filter(expr)


def _retry_env_or_wait(kind: FailureKind, msg: str, attempt: int) -> bool:
    """是否继续重试：每分钟上限冷却 600s；其余 env（网络/5xx）与非 env 均指数退避重试。

    频率超限类 env 由调用方在调用前直接返回（不热重试同一窗口）。
    """
    if kind is FailureKind.ENV and IP_BAN_ERROR_MSG in msg:
        logger.warning("IP per-minute ban, cooling 600s (attempt %d)", attempt + 1)
        time.sleep(600)
        return True
    time.sleep(2 ** attempt)
    return True


def fetch_code_range(
    pro, code: str, start: date, end: date,
    bucket=None, cancel_check=None, max_retries: int = 3,
) -> FetchResult:
    """按股票拉 [start, end]（daily + adj_factor），返回带分类的结果。"""
    ts_code = _to_ts_code(code)
    start_s, end_s = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    last_error = ""
    for attempt in range(max_retries):
        if bucket is not None and not bucket.acquire(timeout=60.0, cancel_check=cancel_check):
            return FetchResult(None, FailureKind.ENV, "令牌桶超时或被取消")
        try:
            resp = pro.daily(ts_code=ts_code, start_date=start_s, end_date=end_s, freq="D")
            df = _response_to_df(resp, code)
            adj = pro.adj_factor(ts_code=ts_code, start_date=start_s, end_date=end_s)
            return FetchResult(_attach_adj_factor(df, adj), None)
        except Exception as e:
            last_error = str(e)
            kind = classify_error(last_error)
            if kind is FailureKind.ENV and RATE_LIMIT_MSG in last_error:
                logger.warning("Rate limit on %s: %s", code, last_error)
                return FetchResult(None, kind, last_error)
            if not _retry_env_or_wait(kind, last_error, attempt):
                return FetchResult(None, kind, last_error)
    return FetchResult(None, classify_error(last_error) if last_error else FailureKind.UNKNOWN,
                       last_error or "retries exhausted")


def _normalize_day_df(resp) -> pl.DataFrame:
    if isinstance(resp, pl.DataFrame):
        df = resp
    else:
        df = pl.DataFrame(resp.to_dict(orient="list"))
    df = df.rename({c: {"trade_date": "date", "vol": "volume"}.get(c, c)
                    for c in df.columns if c in ("trade_date", "vol")})
    if df.is_empty():
        return df
    df = df.with_columns(
        pl.col("date").cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d")
    )
    for col in ["open", "high", "low", "close", "volume", "amount", "pre_close"]:
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Float64))
    df = df.with_columns(
        pl.lit(1.0).cast(pl.Float64).alias("adj_factor"),
        pl.lit(False).cast(pl.Boolean).alias("is_suspended"),
        pl.col("ts_code").str.slice(0, 6).alias("code"),
    )
    cols = ["code", "date", "open", "high", "low", "close", "volume",
            "amount", "pre_close", "adj_factor", "is_suspended"]
    df = df.select([c for c in cols if c in df.columns]).sort("date")
    return df


def fetch_day_by_date(
    pro, day: date, bucket=None, cancel_check=None, max_retries: int = 3,
) -> FetchResult:
    """按交易日拉全市场（daily + adj_factor），各重试 max_retries 次。"""
    day_s = day.strftime("%Y%m%d")
    last_error = ""
    for attempt in range(max_retries):
        if bucket is not None and not bucket.acquire(timeout=60.0, cancel_check=cancel_check):
            return FetchResult(None, FailureKind.ENV, "令牌桶超时或被取消")
        try:
            resp = pro.daily(trade_date=day_s)
            if resp is None:
                return FetchResult(None, FailureKind.UNKNOWN, "daily 返回 None")
            if (isinstance(resp, pl.DataFrame) and resp.is_empty()) or (
                not isinstance(resp, pl.DataFrame) and not resp.to_dict(orient="list")
            ):
                return FetchResult(pl.DataFrame(), FailureKind.OK_EMPTY, None)
            df = _normalize_day_df(resp)
            adj = pro.adj_factor(trade_date=day_s)
            return FetchResult(_attach_adj_factor(df, adj), None)
        except Exception as e:
            last_error = str(e)
            kind = classify_error(last_error)
            if kind is FailureKind.ENV and RATE_LIMIT_MSG in last_error:
                logger.warning("Rate limit on day %s: %s", day, last_error)
                return FetchResult(None, kind, last_error)
            if not _retry_env_or_wait(kind, last_error, attempt):
                return FetchResult(None, kind, last_error)
    return FetchResult(None, classify_error(last_error) if last_error else FailureKind.UNKNOWN,
                       last_error or "retries exhausted")
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/infrastructure/test_fetch.py -v`
Expected: PASS（12 个）

- [ ] **Step 5: 提交**

```bash
git add trendradar/infrastructure/tushare/fetch.py tests/infrastructure/test_fetch.py
git commit -m "feat: fetch.py 抓取原语迁移 + FailureKind 分类（spec §3.7）"
```

---

### Task 7: infrastructure/tushare/stocklist.py —— L∪D + delist_date + EffectiveList（R12/R16）

**Files:**
- Modify: `trendradar/infrastructure/tushare/stocklist.py`
- Test: `tests/infrastructure/test_stocklist.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/infrastructure/test_stocklist.py
from datetime import date

import pandas as pd
import polars as pl

from trendradar.infrastructure.tushare.stocklist import (
    build_effective_list,
    normalize_stock_meta,
)


def _meta_df(rows):
    return pl.DataFrame(rows)


META = _meta_df([
    {"ts_code": "000001.SZ", "code": "000001", "name": "平安银行",
     "list_date": date(1991, 4, 3), "delist_date": None},
    {"ts_code": "000004.SZ", "code": "000004", "name": "国华网安",
     "list_date": date(1991, 1, 14), "delist_date": date(2026, 7, 13)},
    {"ts_code": "920099.BJ", "code": "920099", "name": "瑞华技术",
     "list_date": date(2020, 7, 27), "delist_date": None},      # 北交所：永久剔除
    {"ts_code": "300001.SZ", "code": "300001", "name": "特锐德",
     "list_date": date(2009, 10, 30), "delist_date": None},     # 创业板
    {"ts_code": "688001.SH", "code": "688001", "name": "华兴源创",
     "list_date": date(2019, 7, 22), "delist_date": None},       # 科创板
    {"ts_code": "301999.SZ", "code": "301999", "name": "未来上市",
     "list_date": date(2027, 1, 1), "delist_date": None},        # 晚于 latest：剔除
])

LATEST = date(2026, 8, 27)


def test_effective_list_excludes_bj_and_future_listed():
    eff = build_effective_list(META, exclude_boards=[], latest_tradeable=LATEST)
    assert "920099" not in eff.codes          # .BJ 永久剔除
    assert "301999" not in eff.codes          # list_date > latest
    assert set(eff.codes) == {"000001", "000004", "300001", "688001"}


def test_effective_list_exclude_boards_gem_star():
    eff = build_effective_list(META, exclude_boards=["gem", "star"], latest_tradeable=LATEST)
    assert set(eff.codes) == {"000001", "000004"}


def test_effective_expected_on_handles_delisting():
    eff = build_effective_list(META, exclude_boards=[], latest_tradeable=LATEST)
    # 2026-07-13 之前：4 只应市；退市日当天仍计；之后 3 只
    assert eff.expected_on(date(2026, 7, 10)) == 4
    assert eff.expected_on(date(2026, 7, 13)) == 4
    assert eff.expected_on(date(2026, 7, 14)) == 3


def test_effective_clamped_range():
    eff = build_effective_list(META, exclude_boards=[], latest_tradeable=LATEST)
    assert eff.clamped_range("000001") == (date(2015, 1, 1), LATEST)      # BASELINE 钳制
    assert eff.clamped_range("000004") == (date(2015, 1, 1), date(2026, 7, 13))  # 退市钳制
    assert eff.clamped_range("920099") is None


def test_normalize_meta_parses_dates_and_empty_delist():
    raw = pd.DataFrame([
        {"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行",
         "area": "深圳", "industry": "银行", "market": "主板",
         "list_date": "19910403", "delist_date": None},
        {"ts_code": "000004.SZ", "symbol": "000004", "name": "国华网安",
         "area": "深圳", "industry": "软件", "market": "主板",
         "list_date": "19910114", "delist_date": "20260713"},
    ])
    df = normalize_stock_meta([raw])
    assert df.height == 2
    assert set(df.columns) >= {"ts_code", "code", "name", "list_date", "delist_date"}
    row = df.filter(pl.col("code") == "000004").row(named=True)
    assert row["list_date"] == date(1991, 1, 14)
    assert row["delist_date"] == date(2026, 7, 13)
    row0 = df.filter(pl.col("code") == "000001").row(named=True)
    assert row0["delist_date"] is None


def test_sync_stock_list_calls_l_and_d(tmp_path, monkeypatch):
    """R12：stock_basic 调用 L 与 D 两次并合并。"""
    import trendradar.infrastructure.tushare.stocklist as stocklist

    calls = []

    class FakePro:
        def stock_basic(self, exchange, list_status, fields):
            calls.append(list_status)
            return pd.DataFrame([{
                "ts_code": f"00000{1 if list_status == 'L' else 9}.SZ",
                "symbol": f"00000{1 if list_status == 'L' else 9}",
                "name": "X", "area": "", "industry": "", "market": "主板",
                "list_date": "19910403",
                "delist_date": None if list_status == "L" else "20260713",
            }])

    monkeypatch.setattr(stocklist, "get_pro", lambda: FakePro())
    bars_dir = tmp_path / "market" / "bars"
    df = stocklist.sync_stock_list(bars_dir)
    assert calls == ["L", "D"]
    assert df.height == 2
    assert "delist_date" in df.columns
    saved = pl.read_parquet(tmp_path / "market" / "stock_meta.parquet")
    assert saved.height == 2
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/infrastructure/test_stocklist.py -v`
Expected: FAIL（ImportError: build_effective_list / normalize_stock_meta）

- [ ] **Step 3: 实现 —— 整体重写 `stocklist.py`**

```python
# trendradar/infrastructure/tushare/stocklist.py
"""Tushare 股票清单：L ∪ D 两次调用 + delist_date + 有效清单。见 spec §5/§3.6。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping

import polars as pl

from trendradar.domain.market.sync.spec import BASELINE_START
from trendradar.infrastructure.tushare.client import get_pro
from trendradar.infrastructure.tushare.fetch import EXCLUDE_BOARD_PREFIXES

logger = logging.getLogger(__name__)

_FIELDS = "ts_code,symbol,name,area,industry,market,list_date,delist_date"


def normalize_stock_meta(frames: list) -> pl.DataFrame:
    """合并 L/D 两次响应为统一 schema（code/ts_code/list_date/delist_date…）。"""
    dfs = []
    for data in frames:
        if data is None or not data.to_dict(orient="list"):
            continue
        f = pl.DataFrame(data.to_dict(orient="list"))
        # L 帧的 delist_date 全空会推断成 Null 类型，与 D 帧的 Utf8 无法直接
        # concat —— 统一先转字符串，日期解析放到合并之后
        for col in ("list_date", "delist_date"):
            if col in f.columns:
                f = f.with_columns(pl.col(col).cast(pl.Utf8, strict=False))
        dfs.append(f)
    if not dfs:
        return pl.DataFrame()
    df = pl.concat(dfs)
    if "symbol" in df.columns:
        df = df.rename({"symbol": "code"})
    for col in ("list_date", "delist_date"):
        if col in df.columns:
            df = df.with_columns(
                pl.col(col).str.strptime(pl.Date, "%Y%m%d", strict=False)
                .alias(col)
            )
    return df.unique(subset=["code"], keep="first")


def sync_stock_list(bars_dir: Path) -> pl.DataFrame:
    """stock_basic(L) + stock_basic(D) 两次调用，写 stock_meta.parquet（原子）。"""
    pro = get_pro()
    frames = [
        pro.stock_basic(exchange="", list_status=s, fields=_FIELDS)
        for s in ("L", "D")
    ]
    df = normalize_stock_meta(frames)
    if df.is_empty():
        logger.warning("stock_basic returned empty for both L and D")
        return df

    output = Path(bars_dir).parent / "stock_meta.parquet"
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = __import__("tempfile").mkstemp(dir=output.parent, suffix=".tmp")
    try:
        __import__("os").close(fd)
        df.write_parquet(tmp)
        __import__("os").replace(tmp, output)
    except BaseException:
        try:
            __import__("os").unlink(tmp)
        except OSError:
            pass
        raise
    return df


@dataclass(frozen=True)
class EffectiveList:
    """有效清单 = L∪D − 北交所 − exclude_boards（与拉取侧同一过滤，§3.8 断言①分母）。"""

    codes: tuple[str, ...]
    list_dates: Mapping[str, date]
    delist_dates: Mapping[str, date | None]
    rows: tuple[tuple[date, date | None], ...]  # (list_date, delist_date)
    _clamped: Mapping[str, tuple[date, date]]

    def expected_on(self, day: date) -> int:
        return sum(
            1 for list_d, delist_d in self.rows
            if list_d <= day and (delist_d is None or delist_d >= day)
        )

    def clamped_range(self, code: str) -> tuple[date, date] | None:
        return self._clamped.get(code)


def build_effective_list(
    meta: pl.DataFrame,
    exclude_boards,
    latest_tradeable: date,
    baseline_start: date = BASELINE_START,
) -> EffectiveList:
    """spec §3.6 待拉清单 ①-④：剔未来上市 / 剔北交所 / 剔排除板块 / 区间钳制。"""
    if meta.is_empty() or latest_tradeable is None:
        return EffectiveList((), {}, {}, (), {})

    prefixes = tuple(
        p for b in (exclude_boards or []) for p in EXCLUDE_BOARD_PREFIXES.get(b, ())
    )
    codes: list[str] = []
    list_dates: dict[str, date] = {}
    delist_dates: dict[str, date | None] = {}
    rows: list[tuple[date, date | None]] = []
    clamped: dict[str, tuple[date, date]] = {}

    for row in meta.iter_rows(named=True):
        ts_code = str(row.get("ts_code") or "")
        if ts_code.endswith(".BJ"):
            continue  # Tushare daily 物理不提供北交所行情
        code = str(row["code"])
        if prefixes and code.startswith(prefixes):
            continue
        list_d = row.get("list_date")
        if list_d is None or list_d > latest_tradeable:
            continue
        delist_d = row.get("delist_date")
        start = max(baseline_start, list_d)
        end = min(latest_tradeable, delist_d) if delist_d is not None else latest_tradeable
        if start > end:
            continue
        codes.append(code)
        list_dates[code] = list_d
        delist_dates[code] = delist_d
        rows.append((list_d, delist_d))
        clamped[code] = (start, end)

    return EffectiveList(tuple(codes), list_dates, delist_dates, tuple(rows), clamped)
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/infrastructure/test_stocklist.py -v`
Expected: PASS（6 个）

- [ ] **Step 5: 回归旧调用方不受影响**

Run: `.venv/bin/pytest tests/ -q`
Expected: 全绿（旧 `sync_stock_list` 调用方只依赖返回值 DataFrame 与 `code` 列，签名不变）

- [ ] **Step 6: 提交**

```bash
git add trendradar/infrastructure/tushare/stocklist.py tests/infrastructure/test_stocklist.py
git commit -m "feat: 股票清单 L∪D + delist_date + 有效清单（北交所 .BJ 剔除）"
```

---

### Task 8: infrastructure/tushare/writer.py —— flush_by_code / 单向换名 / 读回

**Files:**
- Create: `trendradar/infrastructure/tushare/writer.py`
- Test: `tests/infrastructure/test_writer.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/infrastructure/test_writer.py
from datetime import date

import polars as pl

from trendradar.infrastructure.tushare.writer import (
    atomic_write_parquet,
    flush_by_code,
    readback_calendar,
    swap_in_bars,
)


def _rows(code, days, close0=10.0):
    return [
        {"code": code, "date": d, "open": close0, "high": close0 + 1,
         "low": close0 - 1, "close": close0, "volume": 100.0, "amount": 1000.0,
         "adj_factor": 1.0, "is_suspended": False}
        for d in days
    ]


def test_flush_by_code_creates_and_upserts(tmp_path):
    bars = tmp_path / "bars"
    flush_by_code(pl.DataFrame(_rows("000001", [date(2026, 8, 26)])), bars)
    # R19：重拉同日覆盖，历史行不变
    flush_by_code(pl.DataFrame(_rows("000001", [date(2026, 8, 26)], close0=12.0)
                               + _rows("000001", [date(2026, 8, 27)])), bars)
    out = pl.read_parquet(bars / "000001.parquet")
    assert out.height == 2
    by_date = {r["date"]: r for r in out.to_dicts()}
    assert by_date[date(2026, 8, 26)]["close"] == 12.0  # 同日新行覆盖
    assert by_date[date(2026, 8, 27)]["close"] == 10.0


def test_flush_by_code_multiple_codes(tmp_path):
    bars = tmp_path / "bars"
    df = pl.DataFrame(_rows("000001", [date(2026, 8, 27)]) + _rows("000002", [date(2026, 8, 27)]))
    written = flush_by_code(df, bars)
    assert sorted(written) == ["000001", "000002"]


def test_readback_calendar(tmp_path):
    bars = tmp_path / "bars"
    assert readback_calendar(bars) == set()
    flush_by_code(pl.DataFrame(_rows("000001", [date(2026, 8, 26), date(2026, 8, 27)])), bars)
    assert readback_calendar(bars) == {date(2026, 8, 26), date(2026, 8, 27)}


def _setup_dirs(market):
    bars = market / "bars"
    staging = market / "staging"
    bars.mkdir(parents=True)
    staging.mkdir(parents=True)
    return bars, staging


def test_swap_one_way_rename(tmp_path):
    market = tmp_path / "market"
    bars, staging = _setup_dirs(market)
    flush_by_code(pl.DataFrame(_rows("000001", [date(2026, 8, 26)])), bars)
    flush_by_code(pl.DataFrame(_rows("000001", [date(2026, 8, 27)])), staging)

    swap_in_bars(market)

    # 新数据进 bars，上一版进 bars_prev，staging 重建为空
    out = pl.read_parquet(market / "bars" / "000001.parquet")
    assert out["date"].to_list() == [date(2026, 8, 27)]
    prev = pl.read_parquet(market / "bars_prev" / "000001.parquet")
    assert prev["date"].to_list() == [date(2026, 8, 26)]
    assert (market / "staging").is_dir()
    assert list((market / "staging").glob("*.parquet")) == []


def test_swap_second_round_overwrites_bars_prev(tmp_path):
    # R20：第二次全量重建不得被上一版残留"续传"；bars_prev 只留最近一版
    market = tmp_path / "market"
    bars, staging = _setup_dirs(market)
    flush_by_code(pl.DataFrame(_rows("000001", [date(2026, 8, 25)])), bars)
    flush_by_code(pl.DataFrame(_rows("000001", [date(2026, 8, 26)])), staging)
    swap_in_bars(market)

    flush_by_code(pl.DataFrame(_rows("000001", [date(2026, 8, 27)])), market / "staging")
    swap_in_bars(market)

    out = pl.read_parquet(market / "bars" / "000001.parquet")
    assert out["date"].to_list() == [date(2026, 8, 27)]
    prev = pl.read_parquet(market / "bars_prev" / "000001.parquet")
    assert prev["date"].to_list() == [date(2026, 8, 26)]  # 上一版被覆盖，非 08-25
    assert list((market / "staging").glob("*.parquet")) == []


def test_atomic_write_no_partial(tmp_path):
    target = tmp_path / "a.parquet"
    df = pl.DataFrame({"x": [1]})
    atomic_write_parquet(df, target)
    assert pl.read_parquet(target)["x"].to_list() == [1]
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/infrastructure/test_writer.py -v`
Expected: FAIL（ImportError: writer）

- [ ] **Step 3: 实现**

```python
# trendradar/infrastructure/tushare/writer.py
"""文件层写入：原子写 / flush_by_code（按日 upsert 幂等）/ 单向换名 / 读回。见 spec §3.6。"""

from __future__ import annotations

import ctypes
import os
import shutil
import tempfile
from datetime import date
from pathlib import Path

import polars as pl

from trendradar.infrastructure.tushare.fetch import _align_columns

_AT_FDCWD = -100
_SYS_RENAMEAT2_X86_64 = 316
_RENAME_EXCHANGE = 2


def atomic_write_parquet(df: pl.DataFrame, target: Path) -> None:
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


def flush_by_code(all_days: pl.DataFrame, bars_dir: Path) -> list[str]:
    """按 code 分组：每个受影响文件读一次 + 按日期 upsert + 原子写一次（INV-4 幂等）。"""
    if all_days.is_empty():
        return []
    bars_dir = Path(bars_dir)
    bars_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for code in all_days["code"].unique().to_list():
        group = all_days.filter(pl.col("code") == code)
        target = bars_dir / f"{code}.parquet"
        if target.exists():
            local = _align_columns(pl.read_parquet(target), group)
            merged = pl.concat(
                [local.filter(~pl.col("date").is_in(group["date"])), group]
            ).sort("date")
        else:
            merged = group.sort("date")
        atomic_write_parquet(merged, target)
        written.append(str(code))
    return written


def readback_calendar(bars_dir: Path) -> set[date]:
    """scan 全库实测日历（所有文件的日期并集）。"""
    files = sorted(Path(bars_dir).glob("*.parquet"))
    if not files:
        return set()
    s = (
        pl.scan_parquet([str(p) for p in files])
        .select(pl.col("date"))
        .unique()
        .collect()
    )
    return set(s["date"].to_list())


def _try_exchange(a: Path, b: Path) -> bool:
    """renameat2(RENAME_EXCHANGE) 原子交换；平台不支持返回 False。"""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        rc = libc.syscall(
            ctypes.c_long(_SYS_RENAMEAT2_X86_64),
            ctypes.c_int(_AT_FDCWD), os.fsencode(a),
            ctypes.c_int(_AT_FDCWD), os.fsencode(b),
            ctypes.c_uint(_RENAME_EXCHANGE),
        )
        return rc == 0
    except Exception:
        return False


def swap_in_bars(market_dir: Path) -> None:
    """单向换名：bars → bars_prev、staging → bars、重建空 staging（spec §3.6）。

    优先 renameat2 原子交换（bars 无缺失窗口）；回退两步 rename
    （毫秒级窗口，崩溃时以 bars_prev 手动恢复，§9）。
    """
    market_dir = Path(market_dir)
    bars = market_dir / "bars"
    staging = market_dir / "staging"
    prev = market_dir / "bars_prev"

    bars.mkdir(parents=True, exist_ok=True)   # 首次建库时 bars 可能尚不存在
    if prev.exists():
        shutil.rmtree(prev)
    if _try_exchange(bars, staging):
        os.replace(staging, prev)          # 交换后旧版落在 staging 位
    else:
        os.replace(bars, prev)
        os.replace(staging, bars)
    staging.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/infrastructure/test_writer.py -v`
Expected: PASS（7 个）

- [ ] **Step 5: 提交**

```bash
git add trendradar/infrastructure/tushare/writer.py tests/infrastructure/test_writer.py
git commit -m "feat: writer 原子写/flush_by_code/单向换名（renameat2 优先）/读回"
```

---

### Task 9: infrastructure/tushare/runner.py（增量执行器）—— run_incremental（R5/R6/R14 执行侧）

**Files:**
- Create: `trendradar/infrastructure/tushare/runner.py`
- Test: `tests/infrastructure/test_runner.py`

runner 只执行与统计，不做任何决策（spec §3.1）。本任务实现增量部分。

- [ ] **Step 1: 写失败测试**

```python
# tests/infrastructure/test_runner.py
from datetime import date

import pandas as pd
import polars as pl

from trendradar.domain.market.sync.spec import FailureKind
from trendradar.infrastructure.tushare.runner import run_incremental
from trendradar.infrastructure.tushare.stocklist import EffectiveList


def _eff(expected_counts: dict[date, int]) -> EffectiveList:
    # 各日 expected 相同（测试场景均如此）：行数取其一，而非逐日累加
    rows = [(date(2010, 1, 1), None)] * max(expected_counts.values())
    return EffectiveList((), {}, {}, tuple(rows), {})


def _day_resp(day: date, n_stocks: int) -> pd.DataFrame:
    return pd.DataFrame([
        {"ts_code": f"{i:06d}.SZ", "trade_date": day.strftime("%Y%m%d"),
         "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
         "vol": 100, "amount": 1000}
        for i in range(n_stocks)
    ])


class FakeAdj:
    def to_dict(self, orient):
        return {}


class DaySeqPro:
    """按日期返回预设行数；未知日期返回空。"""

    def __init__(self, day_counts: dict[date, int], fail_days: dict[date, str] | None = None):
        self.day_counts = day_counts
        self.fail_days = fail_days or {}
        self.calls = []

    def daily(self, **kwargs):
        day = kwargs["trade_date"]
        self.calls.append(day)
        if day in self.fail_days:
            raise RuntimeError(self.fail_days[day])
        n = self.day_counts.get(date.fromisoformat(
            f"{day[:4]}-{day[4:6]}-{day[6:]}"))
        if not n:
            return pd.DataFrame()
        return _day_resp(date.fromisoformat(f"{day[:4]}-{day[4:6]}-{day[6:]}"), n)

    def adj_factor(self, **kwargs):
        return FakeAdj()


D1, D2, D3 = date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27)


def test_run_incremental_all_pass():
    pro = DaySeqPro({D1: 10, D2: 10, D3: 10})
    result = run_incremental(pro, [D1, D2, D3], effective=_eff({D1: 10, D2: 10, D3: 10}),
                             exclude_boards=None)
    assert not result.aborted
    assert result.claimed_days == [D1, D2, D3]
    assert result.doubtful_days == []
    assert result.all_days.height == 30


def test_run_incremental_doubtful_day_still_written_not_claimed():
    # R6：0.5 比值 → 写盘不入账
    pro = DaySeqPro({D1: 10, D2: 5, D3: 10})
    result = run_incremental(pro, [D1, D2, D3], effective=_eff({D1: 10, D2: 10, D3: 10}),
                             exclude_boards=None)
    assert not result.aborted
    assert result.claimed_days == [D1, D3]
    assert result.doubtful_days == [D2]
    assert result.all_days.height == 25  # doubtful 日数据照常累积


def test_run_incremental_env_failure_aborts_batch():
    # R5：第 2 天网络失败 → 立即中止，不拉后续天
    pro = DaySeqPro({D1: 10, D3: 10}, fail_days={D2.strftime("%Y%m%d"): "Connection aborted"})
    import trendradar.infrastructure.tushare.fetch as fetch
    import unittest.mock as mock
    with mock.patch.object(fetch.time, "sleep", lambda s: None):
        result = run_incremental(pro, [D1, D2, D3],
                                 effective=_eff({D1: 10, D2: 10, D3: 10}),
                                 exclude_boards=None)
    assert result.aborted
    assert result.failure_kind is FailureKind.ENV
    assert D3.strftime("%Y%m%d") not in pro.calls  # 后续天未拉


def test_run_incremental_cancel_aborts():
    pro = DaySeqPro({D1: 10, D2: 10})
    result = run_incremental(pro, [D1, D2], effective=_eff({D1: 10, D2: 10}),
                             exclude_boards=None, cancel_check=lambda: True)
    assert result.aborted
    assert result.cancelled


def test_run_incremental_empty_response_is_doubtful():
    # 幽灵日（官方日历有、接口 0 行）：写不进任何行、不入账
    pro = DaySeqPro({D1: 10})  # D2 返回空
    result = run_incremental(pro, [D1, D2], effective=_eff({D1: 10, D2: 10}),
                             exclude_boards=None)
    assert not result.aborted
    assert result.claimed_days == [D1]
    assert result.doubtful_days == [D2]
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/infrastructure/test_runner.py -v`
Expected: FAIL（ImportError: runner）

- [ ] **Step 3: 实现（增量部分）**

```python
# trendradar/infrastructure/tushare/runner.py
"""同步执行器：只执行与统计，不做任何决策（spec §3.1）。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import polars as pl

from trendradar.domain.market.sync.selfcheck import doubtful_by_row_count
from trendradar.domain.market.sync.spec import FailureKind
from trendradar.infrastructure.tushare.fetch import (
    fetch_code_range,
    fetch_day_by_date,
    filter_excluded_boards,
    shard_ranges,
    FetchResult,
)
from trendradar.infrastructure.tushare.stocklist import EffectiveList
from trendradar.infrastructure.tushare.writer import atomic_write_parquet


@dataclass
class IncrementalResult:
    claimed_days: list[date] = field(default_factory=list)
    doubtful_days: list[date] = field(default_factory=list)
    all_days: pl.DataFrame | None = None   # aborted 时为 None（丢弃，不写盘）
    aborted: bool = False
    cancelled: bool = False
    failure_kind: FailureKind | None = None
    abort_reason: str | None = None


def run_incremental(
    pro,
    missing_days: list[date],
    effective: EffectiveList,
    exclude_boards,
    bucket=None,
    progress=None,
    cancel_check=None,
) -> IncrementalResult:
    """串行按日拉取（2 次调用/天）。任何非断言①异常立即中止整批（spec §3.5）。"""
    result = IncrementalResult()
    frames: list[pl.DataFrame] = []
    total = len(missing_days)
    for idx, day in enumerate(missing_days, start=1):
        if cancel_check and cancel_check():
            result.aborted = True
            result.cancelled = True
            result.abort_reason = "cancelled"
            return result
        if progress:
            progress(idx, total, str(day))
        fr = fetch_day_by_date(pro, day, bucket=bucket, cancel_check=cancel_check)
        if fr.kind is not None and fr.kind is not FailureKind.OK_EMPTY:
            result.aborted = True
            result.failure_kind = fr.kind
            result.abort_reason = fr.error
            return result
        df = filter_excluded_boards(fr.df if fr.df is not None else pl.DataFrame(),
                                    exclude_boards)
        # 含 doubtful 日：真实交易数据照常累积（INV-4 幂等）；空帧无列，不入 concat
        if df.width > 0:
            frames.append(df)
        doubtful = doubtful_by_row_count({day: df.height}, list(effective.rows))
        if doubtful:
            result.doubtful_days.extend(doubtful)
        else:
            result.claimed_days.append(day)
    result.all_days = pl.concat(frames) if frames else pl.DataFrame()
    return result
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/infrastructure/test_runner.py -v`
Expected: PASS（5 个）

- [ ] **Step 5: 提交**

```bash
git add trendradar/infrastructure/tushare/runner.py tests/infrastructure/test_runner.py
git commit -m "feat: runner 增量执行器（按日串行 + doubtful 累积 + 中止语义）"
```

---

### Task 10: runner.py（全量与补齐执行器）—— run_full / run_backfill（R7/R13/R21 执行侧）

**Files:**
- Modify: `trendradar/infrastructure/tushare/runner.py`
- Test: `tests/infrastructure/test_runner.py`（追加）

- [ ] **Step 1: 追加失败测试**

在 `tests/infrastructure/test_runner.py` 顶部导入处追加 `run_full, run_backfill, StockOutcome`，并追加：

```python
from trendradar.infrastructure.tushare.runner import StockOutcome, run_backfill, run_full
import trendradar.infrastructure.tushare.runner as runner_mod
from unittest import mock


class CodeRangePro:
    """按 (code) 返回预设响应；可指定失败类别。"""

    def __init__(self, codes_ok: list[str], fail: dict[str, str] | None = None):
        self.codes_ok = set(codes_ok)
        self.fail = fail or {}
        self.calls = []

    def daily(self, **kwargs):
        code = kwargs["ts_code"].split(".")[0]
        self.calls.append(code)
        if code in self.fail:
            raise RuntimeError(self.fail[code])
        return pd.DataFrame([{
            "ts_code": kwargs["ts_code"], "trade_date": "20260825",
            "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
            "vol": 100, "amount": 1000,
        }])

    def adj_factor(self, **kwargs):
        return FakeAdj()


def _patch_sleep():
    import trendradar.infrastructure.tushare.fetch as fetch
    return mock.patch.object(fetch.time, "sleep", lambda s: None)


def test_run_full_writes_staging_files(tmp_path):
    staging = tmp_path / "staging"
    pro = CodeRangePro(["000001", "000002"])
    with _patch_sleep():
        outcomes, cancelled = run_full(
            pro,
            [("000001", date(2026, 8, 25), date(2026, 8, 25)),
             ("000002", date(2026, 8, 25), date(2026, 8, 25))],
            staging, max_workers=2,
        )
    assert not cancelled
    assert all(o.ok for o in outcomes)
    assert (staging / "000001.parquet").exists()
    assert (staging / "000002.parquet").exists()


def test_run_full_failure_classification(tmp_path):
    staging = tmp_path / "staging"
    pro = CodeRangePro(["000001"], fail={"000002": "参数错误", "000003": "Connection aborted"})
    with _patch_sleep():
        outcomes, cancelled = run_full(
            pro,
            [("000001", D1, D1), ("000002", D1, D1), ("000003", D1, D1)],
            staging, max_workers=3,
        )
    by_code = {o.code: o for o in outcomes}
    assert by_code["000001"].ok
    assert by_code["000002"].kind is FailureKind.CODE
    assert by_code["000003"].kind is FailureKind.ENV
    assert not (staging / "000002.parquet").exists()


def test_run_full_cancel_preserves_written(tmp_path):
    staging = tmp_path / "staging"
    pro = CodeRangePro(["000001", "000002"])
    flag = {"cancel": False}

    def progress(cur, total, msg):
        flag["cancel"] = True  # 第一条完成后即取消

    with _patch_sleep():
        outcomes, cancelled = run_full(
            pro, [("000001", D1, D1), ("000002", D1, D1)], staging,
            max_workers=1, progress=progress, cancel_check=lambda: flag["cancel"],
        )
    assert cancelled
    # R10：已写入的 staging 文件原地保留（续传）
    assert (staging / "000001.parquet").exists()


def test_run_backfill_merges_into_bars_and_never_touches_ledger(tmp_path):
    # R9 执行侧：补齐只写文件
    bars = tmp_path / "bars"
    pro = CodeRangePro(["000001"])
    with _patch_sleep():
        outcomes = run_backfill(pro, [("000001", D1, D1)], bars)
    assert outcomes[0].ok
    out = pl.read_parquet(bars / "000001.parquet")
    assert out.height == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/infrastructure/test_runner.py -v`
Expected: FAIL（ImportError: run_full / run_backfill）

- [ ] **Step 3: 实现 —— 在 `runner.py` 末尾追加**

```python
@dataclass(frozen=True)
class StockOutcome:
    code: str
    ok: bool
    kind: FailureKind | None = None   # ok=True 时可为 OK_EMPTY；失败时为分类
    error: str | None = None


def run_full(
    pro,
    tasks: list[tuple[str, date, date]],
    staging_dir: Path,
    bucket=None,
    progress=None,
    cancel_check=None,
    max_workers: int = 6,
) -> tuple[list[StockOutcome], bool]:
    """全量执行：逐只写 staging（本地 bars 一字不动）。返回 (outcomes, cancelled)。

    取消时立即停止派发；已写入的 staging 文件原地保留供续传（spec §3.6）。
    """
    staging_dir = Path(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    outcomes: list[StockOutcome] = []
    cancelled = False
    total = len(tasks)

    def fetch_one(code: str, start: date, end: date) -> StockOutcome:
        frames = []
        for seg_start, seg_end in shard_ranges(start, end):
            if cancel_check and cancel_check():
                return StockOutcome(code, False, FailureKind.ENV, "cancelled")
            fr = fetch_code_range(pro, code, seg_start, seg_end,
                                  bucket=bucket, cancel_check=cancel_check)
            if fr.kind is not None and fr.kind is not FailureKind.OK_EMPTY:
                return StockOutcome(code, False, fr.kind, fr.error)
            if fr.df is not None and not fr.df.is_empty():
                frames.append(fr.df)
        if not frames:
            return StockOutcome(code, True, FailureKind.OK_EMPTY)  # 钳制后仍空：视为成功
        df = pl.concat(frames).sort("date")
        atomic_write_parquet(df, staging_dir / f"{code}.parquet")
        return StockOutcome(code, True)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_one, c, s, e): c for c, s, e in tasks}
        for i, fut in enumerate(as_completed(futures), start=1):
            code = futures[fut]
            try:
                outcome = fut.result()
            except Exception as e:
                outcome = StockOutcome(code, False, FailureKind.UNKNOWN, str(e))
            outcomes.append(outcome)
            if progress:
                progress(i, total, code)
            if cancel_check and cancel_check():
                cancelled = True
                for pending in futures:
                    pending.cancel()
                break
    return outcomes, cancelled


def run_backfill(
    pro,
    tasks: list[tuple[str, date, date]],
    bars_dir: Path,
    bucket=None,
    progress=None,
    cancel_check=None,
) -> list[StockOutcome]:
    """指定代码补齐（INV-3：永不触碰日账本），串行，直接 upsert 进 bars。"""
    from trendradar.infrastructure.tushare.fetch import _align_columns

    bars_dir = Path(bars_dir)
    outcomes: list[StockOutcome] = []
    total = len(tasks)
    for idx, (code, start, end) in enumerate(tasks, start=1):
        if cancel_check and cancel_check():
            outcomes.append(StockOutcome(code, False, FailureKind.ENV, "cancelled"))
            break
        if progress:
            progress(idx, total, code)
        frames = []
        failed: StockOutcome | None = None
        for seg_start, seg_end in shard_ranges(start, end):
            fr = fetch_code_range(pro, code, seg_start, seg_end,
                                  bucket=bucket, cancel_check=cancel_check)
            if fr.kind is not None and fr.kind is not FailureKind.OK_EMPTY:
                failed = StockOutcome(code, False, fr.kind, fr.error)
                break
            if fr.df is not None and not fr.df.is_empty():
                frames.append(fr.df)
        if failed is not None:
            outcomes.append(failed)
            continue
        if frames:
            df = pl.concat(frames).sort("date")
            target = bars_dir / f"{code}.parquet"
            if target.exists():
                local = _align_columns(pl.read_parquet(target), df)
                merged = pl.concat(
                    [local.filter(~pl.col("date").is_in(df["date"])), df]
                ).sort("date")
            else:
                merged = df
            bars_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_parquet(merged, target)
        outcomes.append(StockOutcome(code, True))
    return outcomes
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/infrastructure/test_runner.py -v`
Expected: PASS（9 个）

- [ ] **Step 5: 提交**

```bash
git add trendradar/infrastructure/tushare/runner.py tests/infrastructure/test_runner.py
git commit -m "feat: runner 全量执行器（6 线程写 staging）与补齐执行器"
```

---

### Task 11: app/services/market_sync/commit.py —— 单事务落账

**Files:**
- Create: `trendradar/app/services/market_sync/__init__.py`（空）
- Create: `trendradar/app/services/market_sync/commit.py`
- Test: `tests/app/test_sync_commit.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/app/test_sync_commit.py
from datetime import date

import pytest

from trendradar.app.services.market_sync.commit import commit_full, commit_incremental
from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema
from trendradar.infrastructure.storage.sync_store import SyncStore


@pytest.fixture()
def store(tmp_path):
    sc = StorageConnection(tmp_path)
    init_schema(sc.connect())
    return SyncStore(tmp_path)


def test_commit_incremental_adds_claimed_and_persists_doubtful(store):
    commit_incremental(store, claimed_days=[date(2026, 8, 26)],
                       doubtful_days=[date(2026, 8, 25)])
    assert store.done_days() == {date(2026, 8, 26)}
    assert store.doubtful_days() == [date(2026, 8, 25)]
    assert store.ledger_suspect() is False


def test_commit_incremental_atomic_on_failure(store):
    class BoomStore(SyncStore):
        def set_doubtful_days(self, days):
            raise RuntimeError("boom")

    boom = BoomStore(store._sc.storage_root)
    with pytest.raises(RuntimeError):
        commit_incremental(boom, claimed_days=[date(2026, 8, 26)], doubtful_days=[])
    assert store.done_days() == set()  # 事务回滚，整体不动


def test_commit_full_replaces_and_clears_suspect(store):
    store.add_done_days([date(2015, 1, 2)])      # 旧账
    store.set_ledger_suspect(True)
    commit_full(store, covered_days=[date(2015, 1, 5), date(2015, 1, 6)],
                doubtful_days=[date(2015, 7, 8)])
    assert store.done_days() == {date(2015, 1, 5), date(2015, 1, 6)}
    assert store.ledger_suspect() is False        # 一次全量成功后清除
    assert store.doubtful_days() == [date(2015, 7, 8)]
    assert store.get_meta("last_full_success_at") is not None
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/app/test_sync_commit.py -v`
Expected: FAIL（ImportError: commit）

- [ ] **Step 3: 实现**

```python
# trendradar/app/services/market_sync/commit.py
"""落账：单事务写账本（spec §3.1 "临时副本 → 写盘 → 读回校验 → 单事务落账" 的最后一步）。"""

from __future__ import annotations

from datetime import date, datetime, timezone

from trendradar.infrastructure.storage.sync_store import SyncStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def commit_incremental(
    store: SyncStore,
    claimed_days: list[date],
    doubtful_days: list[date],
) -> None:
    """增量批：单事务写声称日 + 更新 doubtful。失败则整体回滚。"""
    now = _now_iso()
    with store.transaction() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO sync_done_days (trade_date, synced_at) VALUES (?, ?)",
            [(d.isoformat(), now) for d in sorted(set(claimed_days))],
        )
        conn.execute(
            "INSERT INTO sync_meta (key, value) VALUES ('doubtful_days', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_dump_dates(doubtful_days),),
        )


def commit_full(
    store: SyncStore,
    covered_days: list[date],
    doubtful_days: list[date],
) -> None:
    """全量批：单事务用覆盖区间（− doubtful）替换 sync_done_days，
    清 ledger_suspect、记 last_full_success_at（spec §3.4/§3.6）。"""
    now = _now_iso()
    with store.transaction() as conn:
        conn.execute("DELETE FROM sync_done_days")
        conn.executemany(
            "INSERT INTO sync_done_days (trade_date, synced_at) VALUES (?, ?)",
            [(d.isoformat(), now) for d in sorted(set(covered_days))],
        )
        conn.execute(
            "INSERT INTO sync_meta (key, value) VALUES ('doubtful_days', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_dump_dates(doubtful_days),),
        )
        conn.execute(
            "INSERT INTO sync_meta (key, value) VALUES ('ledger_suspect', '0') "
            "ON CONFLICT(key) DO UPDATE SET value = '0'"
        )
        conn.execute(
            "INSERT INTO sync_meta (key, value) VALUES ('last_full_success_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (now,),
        )


def _dump_dates(days) -> str:
    import json
    return json.dumps(sorted(set(d.isoformat() for d in days)))
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/app/test_sync_commit.py -v`
Expected: PASS（3 个）

- [ ] **Step 5: 提交**

```bash
git add trendradar/app/services/market_sync/ tests/app/test_sync_commit.py
git commit -m "feat: 单事务落账（增量追加 / 全量替换 + 清 suspect）"
```

---

### Task 12: executor.py 互斥集合化 + 终态不覆写；JobContext.store（R11 executor 部分）

**Files:**
- Modify: `trendradar/app/jobs/executor.py:36-47`（互斥）、`:99-131`（终态）
- Modify: `trendradar/app/jobs/context.py`
- Test: `tests/app/test_executor_mutex.py`（重写）

- [ ] **Step 1: 更新测试（先失败）**

整体替换 `tests/app/test_executor_mutex.py`：

```python
import time

import pytest

from trendradar.app.jobs.executor import JobExecutor
from trendradar.app.jobs.persistence import JobStore
from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema


def _executor(tmp_path):
    sc = StorageConnection(tmp_path)
    init_schema(sc.connect())
    return JobExecutor(JobStore(sc.db_path))


def test_second_bars_sync_rejected_while_running(tmp_path):
    ex = _executor(tmp_path)
    first = ex.submit("market_bars_sync", lambda ctx: time.sleep(1), {})
    try:
        with pytest.raises(RuntimeError):
            ex.submit("market_bars_sync", lambda ctx: None, {})
    finally:
        ex.shutdown(wait=True)


def test_bars_sync_and_backfill_mutually_exclusive(tmp_path):
    ex = _executor(tmp_path)
    ex.submit("market_bars_sync", lambda ctx: time.sleep(1), {})
    try:
        with pytest.raises(RuntimeError):
            ex.submit("market_backfill_codes", lambda ctx: None, {})
        with pytest.raises(RuntimeError):
            ex.submit("market_backfill_codes", lambda ctx: None, {})
    finally:
        ex.shutdown(wait=True)


def test_other_job_types_not_excluded(tmp_path):
    ex = _executor(tmp_path)
    ex.submit("market_bars_sync", lambda ctx: time.sleep(1), {})
    job_id = ex.submit("selection", lambda ctx: None, {})
    try:
        assert job_id
    finally:
        ex.shutdown(wait=True)


def test_worker_terminal_state_not_overwritten_on_cancel(tmp_path):
    """spec §3.9 坑 2：worker 已写 failed('Cancelled by user') 则不覆写为 cancelled。"""
    ex = _executor(tmp_path)

    def worker(ctx):
        ctx.fail("Cancelled by user")

    job_id = ex.submit("market_bars_sync", worker, {})
    ex.cancel(job_id)
    ex._jobs[job_id].future.result(timeout=5)
    try:
        state = ex.get_state(job_id)
        assert state["status"] == "failed"
        assert state["error"] == "Cancelled by user"
    finally:
        ex.shutdown(wait=True)


def test_plain_cancel_still_yields_cancelled(tmp_path):
    ex = _executor(tmp_path)
    job_id = ex.submit("market_bars_sync", lambda ctx: time.sleep(1), {})
    ex.cancel(job_id)
    ex._jobs[job_id].future.result(timeout=5)
    try:
        assert ex.get_state(job_id)["status"] == "cancelled"
    finally:
        ex.shutdown(wait=True)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/app/test_executor_mutex.py -v`
Expected: FAIL（新 job_type 不在互斥集 / 终态被覆写）

- [ ] **Step 3: 实现**

`trendradar/app/jobs/executor.py` 修改：

1. 模块级新增：

```python
EXCLUSIVE_JOB_TYPES = frozenset({"market_bars_sync", "market_backfill_codes"})
```

2. `submit` 中 `if job_type == "market_sync":` 改为：

```python
        if job_type in EXCLUSIVE_JOB_TYPES:
            # 检查 + 注册必须在同一把锁内，否则并发提交会双跑（TOCTOU）
            with self._lock:
                if any(
                    j.job_type in EXCLUSIVE_JOB_TYPES and not j.future.done()
                    for j in self._jobs.values()
                ):
                    raise RuntimeError(f"{job_type} job conflicts with an in-flight sync job")
                job_id = self._store.create_job(job_type, request)
                fut = self._pool.submit(self._run, job_id, job_type, run_fn)
                self._jobs[job_id] = JobState(job_id, job_type, fut)
                return job_id
```

3. `_run` 的成功/异常路径改为尊重 worker 已写入的终态：

```python
        try:
            run_fn(ctx)
            job = self._store.get_job(job_id)
            if job is not None and job["status"] in ("success", "failed", "cancelled"):
                return  # worker 已写终态（含 failed("Cancelled by user")），不覆写
            if self._is_cancelled(job_id):
                self._store.set_status(job_id, "cancelled")
            else:
                ctx.succeed({})
        except Exception as e:
            job = self._store.get_job(job_id)
            if job is not None and job["status"] in ("success", "failed", "cancelled"):
                return
            if self._is_cancelled(job_id):
                self._store.set_status(job_id, "cancelled")
            else:
                ctx.fail(str(e))
```

4. `trendradar/app/jobs/context.py` 追加：

```python
    @property
    def store(self) -> "JobStore":
        return self._store
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/app/test_executor_mutex.py tests/app/test_job_executor.py -v`
Expected: PASS（`test_job_executor.py` 的既有用例也须保持全绿）

- [ ] **Step 5: 提交**

```bash
git add trendradar/app/jobs/executor.py trendradar/app/jobs/context.py tests/app/test_executor_mutex.py
git commit -m "feat: executor 互斥集合化 + 尊重 worker 终态；JobContext.store"
```

---

### Task 13: app/services/market_sync/service.py + market_service.py 门面重写（R1/R2/R3/R5/R6/R7/R8/R9/R10/R13/R14/R17/R18/R20）

**Files:**
- Create: `trendradar/app/services/market_sync/service.py`
- Modify: `trendradar/app/services/market_service.py`（整体重写为薄门面）
- Test: `tests/app/test_market_sync_service.py`（新建）

本任务是全部编排逻辑的落点。测试为 worker 级集成测试（真 SQLite + 真文件 + FakePro），一次性覆盖 R1–R20 中落在编排层的断言。

- [ ] **Step 1: 写失败测试（共享脚手架部分）**

```python
# tests/app/test_market_sync_service.py
from datetime import date, datetime
from zoneinfo import ZoneInfo

import polars as pl
import pytest

from trendradar.app.jobs.persistence import JobStore
from trendradar.app.services.market_sync import service
from trendradar.infrastructure.storage.connection import StorageConnection
from trendradar.infrastructure.storage.schema import init_schema
from trendradar.infrastructure.storage.sync_store import SyncStore

CN = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 8, 27, 18, 0, tzinfo=CN)   # 16:00 后 → 今日可得
CODES = ["000001", "000002", "600000"]
# 官方日历：08-24/25/26/27（周一至周四）+ 年底远日（保证 MAX ≥ today）
CAL = [date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26),
       date(2026, 8, 27), date(2026, 12, 31)]
DONE = [date(2026, 8, 24), date(2026, 8, 25)]   # 缺口 = 26/27


class FakeResp:
    def __init__(self, data: dict):
        self._data = data

    def to_dict(self, orient="records"):
        return self._data


def _resp_cal(days):
    return FakeResp({"cal_date": [d.strftime("%Y%m%d") for d in days],
                     "is_open": [1] * len(days)})


def _resp_day(day, codes):
    n = len(codes)
    return FakeResp({
        "ts_code": [f"{c}.SZ" for c in codes],
        "trade_date": [day.strftime("%Y%m%d")] * n,
        "open": [10.0] * n, "high": [11.0] * n, "low": [9.0] * n,
        "close": [10.5] * n, "vol": [1000.0] * n,
    })


def _resp_meta(codes):
    n = len(codes)
    return FakeResp({
        "ts_code": [f"{c}.SZ" for c in codes], "symbol": list(codes),
        "name": [f"S{c}" for c in codes], "area": [""] * n,
        "industry": [""] * n, "market": [""] * n,
        "list_date": ["20100101"] * n, "delist_date": [None] * n,
    })


class FakePro:
    """可注水的 Tushare pro。daily(trade_date=) → 按日；daily(ts_code=) → 按股。"""

    def __init__(self):
        self.calendar_days = list(CAL)
        self.day_codes = {}        # date -> 该日返回的代码列表
        self.code_days = {}        # code -> 该股的日期列表（范围模式）
        self.raise_cal = None      # trade_cal 抛错
        self.raise_daily = None    # daily(trade_date=) 抛错
        self.raise_daily_codes = set()                        # 按股注水（范围模式）
        self.raise_daily_exc = RuntimeError("频率超限")
        self.daily_calls = 0

    def trade_cal(self, exchange=None, start_date=None, end_date=None):
        if self.raise_cal:
            raise self.raise_cal
        return _resp_cal(self.calendar_days)

    def stock_basic(self, exchange="", list_status=None, fields=None):
        if list_status == "D":
            return FakeResp({})
        return _resp_meta(CODES)

    def daily(self, ts_code=None, trade_date=None, start_date=None, end_date=None, freq=None):
        self.daily_calls += 1
        if trade_date:
            if self.raise_daily:
                raise self.raise_daily
            day = datetime.strptime(trade_date, "%Y%m%d").date()
            codes = self.day_codes.get(day, [])
            return _resp_day(day, codes) if codes else FakeResp({})
        code = ts_code.split(".")[0]
        if code in self.raise_daily_codes:
            raise self.raise_daily_exc
        lo = datetime.strptime(start_date, "%Y%m%d").date()
        hi = datetime.strptime(end_date, "%Y%m%d").date()
        days = [d for d in self.code_days.get(code, []) if lo <= d <= hi]
        if not days:
            return FakeResp({})
        n = len(days)
        return FakeResp({
            "ts_code": [ts_code] * n,
            "trade_date": [d.strftime("%Y%m%d") for d in days],
            "open": [10.0] * n, "high": [11.0] * n, "low": [9.0] * n,
            "close": [10.5] * n, "vol": [1000.0] * n,
        })

    def adj_factor(self, ts_code=None, trade_date=None, start_date=None, end_date=None):
        return FakeResp({})


class FakeCtx:
    def __init__(self, job_id, store, cancel_after=None):
        self.job_id = job_id
        self.store = store
        self.logs = []
        self.status = "running"
        self.result = None
        self.error = None
        self._cancel_after = cancel_after
        self._progress_count = 0

    def log(self, message, level="INFO"):
        self.logs.append(message)

    def update_progress(self, current, total, message=""):
        self._progress_count += 1
        self.logs.append(f"[PROGRESS] {current}/{total} {message}")

    def check_cancelled(self):
        return self._cancel_after is not None and self._progress_count >= self._cancel_after

    def succeed(self, result):
        self.status, self.result = "success", result

    def fail(self, error):
        self.status, self.error = "failed", error


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    storage = tmp_path / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    init_schema(StorageConnection(storage).connect())
    return tmp_path


@pytest.fixture
def job_store(runtime):
    # stage-1 会经 ctx.store 写 sibling job 行 → 独立 db 也要有 jobs 表
    import sqlite3

    db_path = runtime / "storage" / "jobs.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL UNIQUE,
            job_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            started_at TEXT,
            finished_at TEXT,
            request_json TEXT,
            result_json TEXT,
            error_message TEXT
        );
        CREATE TABLE IF NOT EXISTS job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            level TEXT NOT NULL DEFAULT 'INFO',
            message TEXT NOT NULL,
            UNIQUE(job_id, sequence)
        );
        """
    )
    conn.commit()
    conn.close()
    return JobStore(db_path)


@pytest.fixture
def sync_store(runtime):
    return SyncStore(runtime / "storage")


@pytest.fixture
def fake_pro(monkeypatch):
    pro = FakePro()
    monkeypatch.setattr(service, "get_pro", lambda: pro)
    import trendradar.infrastructure.tushare.stocklist as sl
    monkeypatch.setattr(sl, "get_pro", lambda: pro)
    return pro


def run_worker(fake_pro, request, job_store, cancel_after=None):
    ctx = FakeCtx("20260827_180000_market_bars_sync_t1", job_store, cancel_after)
    service.bars_sync_worker(ctx, request, now_cn=NOW)
    return ctx


def seed_calendar_and_done(sync_store):
    sync_store.insert_calendar_days(CAL)
    sync_store.add_done_days(DONE)
```

- [ ] **Step 2: 追加失败测试（断言部分）**

同文件继续：

```python
# ---- R1 / R2 / R3：stage-1 日历 ----

def test_r1_calendar_only_grows(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days([date(2015, 1, 5), date(2026, 12, 31)])
    # 窄请求（新 schema 下已无日期字段，等价于"任何请求"）
    run_worker(fake_pro, {}, job_store)
    days = sync_store.calendar_days()
    assert days >= {date(2015, 1, 5), date(2026, 12, 31)}      # 只增不减
    assert sync_store.max_calendar_day() >= date(2026, 12, 31)  # MAX 不回退
    run_worker(fake_pro, {}, job_store)                         # 连刷幂等
    assert sync_store.calendar_days() == days | set(CAL)


def test_r2_stale_calendar_blocks(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days([date(2026, 8, 20)])        # MAX < today
    fake_pro.raise_cal = RuntimeError("Connection aborted")
    ctx = run_worker(fake_pro, {}, job_store)
    assert ctx.status == "failed"
    assert "日历" in ctx.error
    assert not (runtime / "storage" / "market" / "stock_meta.parquet").exists()


def test_r3_empty_calendar_fails_not_skip(runtime, job_store, sync_store, fake_pro):
    fake_pro.raise_cal = RuntimeError("Connection aborted")
    ctx = run_worker(fake_pro, {}, job_store)
    assert ctx.status == "failed"
    assert "日历未就绪" in ctx.error


# ---- R6 / R14：增量 doubtful 与自愈 ----

def test_r6_incremental_doubtful_day_written_not_booked(runtime, job_store,
                                                        sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    sync_store.add_done_days(DONE)
    d26, d27 = date(2026, 8, 26), date(2026, 8, 27)
    fake_pro.day_codes = {d26: CODES, d27: CODES[:2]}  # 27 日只有 2/3 → 0.667 < 0.75
    ctx = run_worker(fake_pro, {}, job_store)
    assert ctx.status == "failed"
    assert "doubtful" in ctx.error
    done = sync_store.done_days()
    assert d26 in done and d27 not in done
    assert sync_store.doubtful_days() == [d27]
    assert not sync_store.ledger_suspect()
    bars = runtime / "storage" / "market" / "bars"
    df = pl.read_parquet(bars / "000001.parquet")
    assert set(df["date"].to_list()) == {d26, d27}              # doubtful 日数据照常写盘


def test_r14_doubtful_self_heals_next_round(runtime, job_store, sync_store, fake_pro):
    test_r6_incremental_doubtful_day_written_not_booked(runtime, job_store, sync_store, fake_pro)
    d27 = date(2026, 8, 27)
    fake_pro.day_codes = {d27: CODES}                           # 重拉回升
    ctx = run_worker(fake_pro, {}, job_store)
    assert ctx.status == "success"
    assert d27 in sync_store.done_days()
    assert sync_store.doubtful_days() == []


# ---- R5：增量中止与漂移 ----

def test_r5_incremental_abort_writes_nothing(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    sync_store.add_done_days(DONE)
    fake_pro.day_codes = {date(2026, 8, 26): CODES}
    fake_pro.raise_daily = RuntimeError("频率超限")               # env，立即返回不热重试
    ctx = run_worker(fake_pro, {}, job_store)
    assert ctx.status == "failed"
    assert "增量中止" in ctx.error
    assert sync_store.done_days() == set(DONE)
    bars = runtime / "storage" / "market" / "bars"
    assert not bars.exists() or not list(bars.glob("*.parquet"))


# ---- R9：BACKFILL_CODES 不碰日账本 ----

def test_r9_backfill_codes_never_touches_ledger(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    sync_store.add_done_days(DONE)
    sync_store.record_skip_failure("000002", "boom", "code")
    fake_pro.code_days = {"000002": [date(2026, 8, 26), date(2026, 8, 27)]}
    ctx = run_worker(fake_pro, {"codes": ["000002"]}, job_store)
    assert ctx.status == "success"
    assert sync_store.done_days() == set(DONE)                  # INV-3
    assert sync_store.skipped_rows() == []                      # 成功销账
    assert (runtime / "storage" / "market" / "bars" / "000002.parquet").exists()


# ---- R10 / R20：全量换名 ----

def test_r10_full_success_swaps_and_keeps_prev(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    bars = runtime / "storage" / "market" / "bars"
    bars.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"date": [date(2020, 1, 2)], "close": [1.0]}).write_parquet(bars / "OLD.parquet")
    for c in CODES:
        fake_pro.code_days[c] = CAL[:4]
    ctx = run_worker(fake_pro, {"force": True}, job_store)
    assert ctx.status == "success"
    assert (bars / "000001.parquet").exists()
    assert not (bars / "OLD.parquet").exists()
    assert (runtime / "storage" / "market" / "bars_prev" / "OLD.parquet").exists()
    assert not list((runtime / "storage" / "market" / "staging").glob("*.parquet"))
    assert sync_store.done_days() == set(CAL[:4])


def test_r10_full_cancel_keeps_staging_bars_untouched(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    bars = runtime / "storage" / "market" / "bars"
    bars.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"date": [date(2020, 1, 2)], "close": [1.0]}).write_parquet(bars / "OLD.parquet")
    for c in CODES:
        fake_pro.code_days[c] = CAL[:4]
    ctx = run_worker(fake_pro, {"force": True}, job_store, cancel_after=1)
    assert ctx.status == "failed"
    assert "Cancelled" in ctx.error
    assert (bars / "OLD.parquet").exists()                      # 换名前一字未改
    assert not (runtime / "storage" / "market" / "bars_prev").exists()


def test_r20_second_force_full_pulls_everything_again(runtime, job_store, sync_store, fake_pro):
    test_r10_full_success_swaps_and_keeps_prev(runtime, job_store, sync_store, fake_pro)
    before = fake_pro.daily_calls
    for c in CODES:
        fake_pro.code_days[c] = CAL[:4]
    ctx = run_worker(fake_pro, {"force": True}, job_store)
    assert ctx.status == "success"
    assert fake_pro.daily_calls - before >= len(CODES)          # 未被"续传"空转
    prev = runtime / "storage" / "market" / "bars_prev" / "000001.parquet"
    assert prev.exists()                                        # 上一版在 bars_prev


# ---- R7 / R8：熔断与带缺口提交 ----

def test_r7_full_env_breaker_records_nothing(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    fake_pro.code_days = {"000001": CAL[:4]}                    # 1/3 成功 → 66% > 5%
    fake_pro.raise_daily_codes = {"000002", "600000"}           # 其余股限流（立即返回）
    ctx = run_worker(fake_pro, {"force": True}, job_store)
    assert ctx.status == "failed"
    assert "熔断" in ctx.error
    assert sync_store.skipped_rows() == []                      # env 一律不计次
    assert list((runtime / "storage" / "market" / "staging").glob("*.parquet"))  # staging 保留


def test_r8_env_residue_rejects_partial_baseline(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    sync_store.record_skip_failure("000002", "每分钟最多访问该接口", "env")
    for c in CODES:
        fake_pro.code_days[c] = CAL[:4]
    ctx = run_worker(fake_pro, {"force": True, "accept_partial_baseline": True}, job_store)
    assert ctx.status == "failed"
    assert "env" in ctx.error
    assert sync_store.done_days() == set()
    assert not (runtime / "storage" / "market" / "bars" / "000001.parquet").exists()


def test_r8_no_confirm_keeps_staging(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    sync_store.record_skip_failure("000002", "无此股票", "code")
    for c in CODES:
        fake_pro.code_days[c] = CAL[:4]
    ctx = run_worker(fake_pro, {"force": True}, job_store)
    assert ctx.status == "failed"
    assert "accept_partial_baseline" in ctx.error
    assert list((runtime / "storage" / "market" / "staging").glob("*.parquet"))


# ---- R13：全量批单日语义 ----

def test_r13_full_doubtful_day_swapped_but_not_booked(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    # 26 日只有 2/3 只 → 0.667 < 0.75 → doubtful；其余日齐全
    for c in CODES:
        fake_pro.code_days[c] = CAL[:4]
    fake_pro.code_days["600000"] = [d for d in CAL[:4] if d != date(2026, 8, 26)]
    ctx = run_worker(fake_pro, {"force": True}, job_store)
    assert ctx.status == "failed"
    assert "doubtful" in ctx.error
    done = sync_store.done_days()
    assert date(2026, 8, 26) not in done
    assert done == set(CAL[:4]) - {date(2026, 8, 26)}
    assert sync_store.doubtful_days() == [date(2026, 8, 26)]
    assert not sync_store.ledger_suspect()
    bars = runtime / "storage" / "market" / "bars"
    assert (bars / "600000.parquet").exists()                   # 换名照常入库


# ---- R17：换名成功但账本事务失败 ----

def test_r17_commit_failure_after_swap(runtime, job_store, sync_store, fake_pro, monkeypatch):
    sync_store.insert_calendar_days(CAL)
    for c in CODES:
        fake_pro.code_days[c] = CAL[:4]

    def boom(*args, **kwargs):
        raise RuntimeError("txn boom")

    monkeypatch.setattr(service, "commit_full", boom)
    ctx = run_worker(fake_pro, {"force": True}, job_store)
    assert ctx.status == "failed"
    assert "事务" in ctx.error
    bars = runtime / "storage" / "market" / "bars"
    assert (bars / "000001.parquet").exists()                   # 文件已新
    assert sync_store.done_days() == set()                      # 账本仍旧
    assert not sync_store.ledger_suspect()                      # 不置 suspect


# ---- R18：UPTODATE 尾部补齐失败不翻转终态 ----

def test_r18_uptodate_tail_backfill_env_failure_keeps_success(runtime, job_store,
                                                              sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    sync_store.add_done_days(CAL[:4])                           # 无缺口 → UPTODATE
    sync_store.record_skip_failure("000002", "old", "code")
    sync_store.record_skip_failure("000002", "old", "code")
    sync_store.record_skip_failure("000002", "old", "code")     # attempts=3
    fake_pro.raise_daily_codes = {"000002"}                     # 补齐必挂（限流，立即返回）
    ctx = run_worker(fake_pro, {}, job_store)
    assert ctx.status == "success"                              # 主作业终态不翻转
    rows = sync_store.skipped_rows()
    assert len(rows) == 1 and rows[0]["code"] == "000002"       # 缺口与失败记录保留


# ---- R21：全量自检失败的 staging 处置 ----

def test_r21_full_staging_corruption_discards(runtime, job_store, sync_store, fake_pro):
    sync_store.insert_calendar_days(CAL)
    staging = runtime / "storage" / "market" / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    # 预置一个"续传残留"的坏文件（重复日期 → 断言③失败）
    pl.DataFrame({
        "date": [date(2026, 8, 24), date(2026, 8, 24)],
        "open": [1.0] * 2, "high": [1.0] * 2, "low": [1.0] * 2, "close": [1.0] * 2,
    }).write_parquet(staging / "600000.parquet")
    for c in CODES[:2]:
        fake_pro.code_days[c] = CAL[:4]
    ctx = run_worker(fake_pro, {"force": True}, job_store)
    assert ctx.status == "failed"
    assert "③" in ctx.error
    assert sync_store.ledger_suspect()
    assert not list(staging.glob("*.parquet"))                  # 丢弃，不续传坏文件
    # 下一轮从头重拉（含 600000）且成功
    fake_pro.code_days = {c: CAL[:4] for c in CODES}
    ctx = run_worker(fake_pro, {"force": True}, job_store)
    assert ctx.status == "success"


def test_r21_full_readback_missing_day_discards(runtime, job_store, sync_store,
                                                fake_pro, monkeypatch):
    sync_store.insert_calendar_days(CAL)
    for c in CODES:
        fake_pro.code_days[c] = CAL[:4]
    real_readback = service.readback_calendar
    monkeypatch.setattr(service, "readback_calendar",
                        lambda d: real_readback(d) - {date(2026, 8, 25)})
    ctx = run_worker(fake_pro, {"force": True}, job_store)
    assert ctx.status == "failed"
    assert "②" in ctx.error
    assert sync_store.ledger_suspect()
    assert not list((runtime / "storage" / "market" / "staging").glob("*.parquet"))
```

- [ ] **Step 3: 运行确认失败**

Run: `.venv/bin/pytest tests/app/test_market_sync_service.py -v`
Expected: FAIL（ModuleNotFoundError: service.bars_sync_worker）

- [ ] **Step 4: 实现 `trendradar/app/services/market_sync/service.py`**

```python
"""市场数据同步编排：stage-1 日历 + stage-2 行情（spec §3）。

依赖方向：app → infrastructure/domain。唯一有权驱动
runner/writer/commit 的模块。
"""

from __future__ import annotations

import shutil
from datetime import date, datetime
from pathlib import Path

import polars as pl

from trendradar.app.jobs.context import JobContext
from trendradar.app.services.market_sync.commit import commit_full, commit_incremental
from trendradar.domain.market.sync.planner import build_plan
from trendradar.domain.market.sync.selfcheck import (
    coverage_ok,
    doubtful_by_row_count,
    file_structure_ok,
    ledger_subset_ok,
)
from trendradar.domain.market.sync.spec import (
    BASELINE_START,
    FailureKind,
    PlanKind,
    SHANGHAI,
)
from trendradar.infrastructure.storage.sync_store import SyncStore
from trendradar.infrastructure.tushare.calendar import fetch_trade_calendar
from trendradar.infrastructure.tushare.client import get_pro
from trendradar.infrastructure.tushare.rate_limit import TokenBucket
from trendradar.infrastructure.tushare.runner import (
    run_backfill,
    run_full,
    run_incremental,
)
from trendradar.infrastructure.tushare.stocklist import (
    build_effective_list,
    sync_stock_list,
)
from trendradar.infrastructure.tushare.writer import (
    flush_by_code,
    readback_calendar,
    swap_in_bars,
)

FULL_FAIL_RATE_BREAKER = 0.05


def _invalidate_status_cache() -> None:
    # 函数级导入：presenters 反向依赖 service 会成环
    from trendradar.interfaces.api.presenters import invalidate_market_status_cache

    invalidate_market_status_cache()


def register_market_sync_execution(job_id: str) -> None:
    from trendradar.infrastructure.runtime import storage_root
    from trendradar.infrastructure.storage.connection import StorageConnection
    from trendradar.infrastructure.storage.registration import register_execution

    with StorageConnection(storage_root()).connection() as conn:
        register_execution(conn, job_id, "market_bars_sync")
        conn.commit()


def bars_sync_worker(
    ctx: JobContext, request: dict, now_cn: datetime | None = None
) -> None:
    from trendradar.infrastructure.runtime import storage_root

    root = storage_root()
    store = SyncStore(root)
    market_dir = root / "market"
    try:
        _bars_sync_body(ctx, request, store, market_dir, now_cn)
    except Exception as e:  # 意外异常如实 failed，绝不假绿（INV-1）
        ctx.fail(f"意外错误: {e}")
    finally:
        _invalidate_status_cache()


def _bars_sync_body(ctx, request, store, market_dir, now_cn=None):
    now_cn = now_cn or datetime.now(SHANGHAI)
    today_cn = now_cn.date()
    pro = get_pro()
    bars_dir = market_dir / "bars"
    staging_dir = market_dir / "staging"
    register_market_sync_execution(ctx.job_id)

    # ---- stage-1：日历刷新（同步步骤，不经 executor，spec §3.9）----
    if not _stage1_calendar(ctx, store, pro, now_cn):
        if not store.calendar_days():
            ctx.fail("日历未就绪，行情同步已阻断")
        else:
            ctx.fail("官方日历未就绪/过期，拒绝判断新鲜度")
        return

    # 硬检（§3.3）：不满足即 failed，禁止降级继续
    max_day = store.max_calendar_day()
    if max_day is None or max_day < today_cn:
        ctx.fail("官方日历未就绪/过期，拒绝判断新鲜度")
        return

    exclude_boards = request.get("exclude_boards") or []
    meta = sync_stock_list(bars_dir)
    ctx.log(f"股票清单已刷新：{meta.height} 行")

    plan = build_plan(
        today_cn, now_cn, store.calendar_days(), store.done_days(),
        store.ledger_suspect(), request,
    )
    ctx.log(f"决策: {plan.kind.value} —— {plan.reason}")

    progress = lambda cur, total, msg: ctx.update_progress(cur, total, msg)
    cancel_check = lambda: ctx.check_cancelled()

    if plan.kind is PlanKind.BLOCKED:
        ctx.fail(plan.reason)
        return
    if plan.kind is PlanKind.REBUILD_REQUIRED:
        ctx.fail(plan.reason)
        return

    effective = build_effective_list(meta, exclude_boards, plan.latest_tradeable)
    bucket = TokenBucket()

    if plan.kind is PlanKind.BACKFILL_CODES:
        ok = _run_backfill_batch(
            ctx, pro, store, effective, request.get("codes") or [],
            bars_dir, bucket, progress, cancel_check,
        )
        if ok:
            ctx.succeed({"kind": "backfill_codes"})
        else:
            ctx.fail("指定代码补齐存在失败")
        return

    if plan.kind is PlanKind.FULL:
        _run_full(ctx, pro, store, effective, request, plan,
                  market_dir, bars_dir, staging_dir, bucket, progress, cancel_check)
        return

    # ---- UPTODATE / INCREMENTAL ----
    if plan.kind is PlanKind.INCREMENTAL:
        ok, fail_msg = _run_incremental(
            ctx, pro, store, effective, exclude_boards, plan,
            bars_dir, bucket, progress, cancel_check,
        )
    else:
        ok, fail_msg = True, None
        ctx.log(plan.reason)
    if ok:
        _tail_backfill(ctx, pro, store, effective, bars_dir, bucket, progress, cancel_check)
        ctx.succeed({"kind": plan.kind.value, "reason": plan.reason})
    else:
        ctx.fail(fail_msg)


def _stage1_calendar(ctx, store, pro, now_cn) -> bool:
    cal_job_id = ctx.store.create_job("market_calendar_sync", {})
    ctx.store.update_started_at(cal_job_id)
    try:
        fetched = _refresh_calendar(store, pro, now_cn)
    except Exception as e:
        ctx.store.set_status(cal_job_id, "failed", error=str(e))
        ctx.log(f"stage-1 日历刷新失败: {e}", level="ERROR")
        return False
    ctx.store.set_status(cal_job_id, "success", result={"fetched": len(fetched)})
    ctx.log(f"stage-1 日历刷新完成：拉取 {len(fetched)} 日")
    return True


def _refresh_calendar(store, pro, now_cn) -> list[date]:
    """表空 → BASELINE..今年年底；非空 → 只刷今年（各 1 次调用，spec §3.3）。"""
    start = BASELINE_START if store.max_calendar_day() is None else date(now_cn.year, 1, 1)
    end = date(now_cn.year, 12, 31)
    days = fetch_trade_calendar(pro, start, end)
    if not days:
        raise RuntimeError("trade_cal 返回空")
    store.insert_calendar_days(days)
    return days


def _run_incremental(ctx, pro, store, effective, exclude_boards, plan,
                     bars_dir, bucket, progress, cancel_check):
    """增量批。返回 (ok, fail_msg)；终态由调用方统一写（单次终态）。"""
    res = run_incremental(
        pro, plan.missing_days, effective, exclude_boards,
        bucket=bucket, progress=progress, cancel_check=cancel_check,
    )
    if res.cancelled:
        return False, "Cancelled by user"
    if res.aborted:
        kind = res.failure_kind.value if res.failure_kind else "?"
        return False, f"增量中止（{kind}）: {res.abort_reason}"

    all_days = res.all_days if res.all_days is not None else pl.DataFrame()

    # ③ 结构自检（内存副本，按未来文件分组）
    if not _memory_structure_ok(all_days):
        store.set_ledger_suspect(True)
        return False, "自检③失败：内存副本结构异常，整批不落账（已置 ledger_suspect）"

    # 写盘（含 doubtful 日，INV-4 幂等）
    flush_by_code(all_days, bars_dir)

    # ⑤ 读回校验 ②④
    readback = readback_calendar(bars_dir)
    claimed = set(res.claimed_days)
    if not coverage_ok(readback, claimed) or not ledger_subset_ok(claimed, readback):
        store.set_ledger_suspect(True)
        return False, "自检②④失败：读回日历与声称集合漂移（已置 ledger_suspect）"

    # ⑥ 单事务落账（仅声称日；doubtful 合并 = 旧 − 已入账 ∪ 新）
    merged_doubtful = sorted({*store.doubtful_days(), *res.doubtful_days} - claimed)
    commit_incremental(store, sorted(claimed), merged_doubtful)

    if res.doubtful_days:
        return False, (f"{len(res.doubtful_days)} 个交易日行数异常（doubtful）"
                       f"，未入账，可自愈或人工确认入账")
    return True, None


def _memory_structure_ok(all_days: pl.DataFrame) -> bool:
    if all_days.is_empty():
        return True
    for code in all_days["code"].unique().to_list():
        group = all_days.filter(pl.col("code") == code).sort("date")
        if not file_structure_ok(group):
            return False
    return True


def _run_full(ctx, pro, store, effective, request, plan,
              market_dir, bars_dir, staging_dir, bucket, progress, cancel_check):
    staging_dir.mkdir(parents=True, exist_ok=True)
    excluded = set(store.excluded_codes())
    existing = {p.stem for p in staging_dir.glob("*.parquet")}
    tasks = []
    for code in effective.codes:
        if code in excluded or code in existing:
            continue
        rng = effective.clamped_range(code)
        tasks.append((code, rng[0], rng[1]))
    ctx.log(f"全量：待拉 {len(tasks)} 只（续传剔除 {len(existing)}，出列剔除 {len(excluded)}）")

    outcomes, cancelled = run_full(
        pro, tasks, staging_dir,
        bucket=bucket, progress=progress, cancel_check=cancel_check,
    )
    if cancelled:
        ctx.fail("Cancelled by user")
        return

    failed = [o for o in outcomes if not o.ok]
    # 整批熔断（§3.7 主保险）：>5% ⇒ 环境故障 ⇒ 一律不计次，保留 staging
    if tasks and len(failed) / len(tasks) > FULL_FAIL_RATE_BREAKER:
        ctx.fail(f"整批熔断：{len(failed)}/{len(tasks)} 只失败（>5%），"
                 f"判定环境故障，本轮不计次，staging 保留续传")
        return
    for o in failed:  # 健康轮才记失败；env 不计（INV-5）
        if o.kind in (FailureKind.CODE, FailureKind.UNKNOWN):
            store.record_skip_failure(o.code, o.error, o.kind.value)

    covered = sorted(
        d for d in store.calendar_days()
        if BASELINE_START <= d <= plan.latest_tradeable
    )

    # ---- 自检：①②③ 全部对 staging（换名前，spec §3.6/§3.8）----
    readback = readback_calendar(staging_dir)
    if not coverage_ok(readback, set(covered)):
        _discard_staging(staging_dir)
        store.set_ledger_suspect(True)
        ctx.fail("全量自检②失败：staging 读回缺日，丢弃 staging，下轮从头重拉")
        return
    bad = _first_structurally_bad_file(staging_dir)
    if bad is not None:
        _discard_staging(staging_dir)
        store.set_ledger_suspect(True)
        ctx.fail(f"全量自检③失败：{bad} 结构异常，丢弃 staging，下轮从头重拉")
        return

    doubtful = doubtful_by_row_count(_staging_day_rows(staging_dir), list(effective.rows))

    # ---- 带缺口提交约束（§3.7）：卡在换名/落账之前 ----
    skipped = store.skipped_rows()
    if skipped:
        if not request.get("accept_partial_baseline"):
            ctx.fail(f"sync_skipped 非空（{len(skipped)} 只），"
                     f"带缺口提交需显式 accept_partial_baseline=true；staging 保留续传")
            return
        if any(r.get("kind") == FailureKind.ENV.value for r in skipped):
            ctx.fail("sync_skipped 含 env 残留，拒绝带缺口提交，等下轮")
            return

    # ---- 单向换名（§3.6）→ ④ → 单事务落账 ----
    swap_in_bars(market_dir)
    booked = sorted(set(covered) - set(doubtful))
    if not ledger_subset_ok(set(booked), readback_calendar(bars_dir)):
        # 全量批 ④ 失败：不置 suspect（置则逼迫重建已换名成功的数据）
        ctx.fail("全量自检④失败：换名后未落账，下轮增量幂等自愈")
        return
    try:
        commit_full(store, booked, doubtful)
    except Exception as e:
        ctx.fail(f"账本事务失败（文件已换名，下轮增量自愈）: {e}")
        return
    if doubtful:
        ctx.fail(f"{len(doubtful)} 个交易日行数异常（doubtful），未入账，可确认入账")
        return
    ctx.succeed({"kind": "full", "fetched": len(tasks), "failed": len(failed)})


def _discard_staging(staging_dir: Path) -> None:
    shutil.rmtree(staging_dir, ignore_errors=True)
    Path(staging_dir).mkdir(parents=True, exist_ok=True)


def _first_structurally_bad_file(staging_dir: Path) -> str | None:
    for p in sorted(Path(staging_dir).glob("*.parquet")):
        try:
            df = pl.read_parquet(p)
        except Exception:
            return p.name
        if not file_structure_ok(df):
            return p.name
    return None


def _staging_day_rows(staging_dir: Path) -> dict:
    files = sorted(Path(staging_dir).glob("*.parquet"))
    if not files:
        return {}
    s = (
        pl.scan_parquet([str(p) for p in files])
        .group_by("date").agg(pl.len().alias("n")).collect()
    )
    return {row["date"]: row["n"] for row in s.iter_rows(named=True)}


def _run_backfill_batch(ctx, pro, store, effective, codes, bars_dir,
                        bucket, progress, cancel_check) -> bool:
    """INV-3：永不触碰日账本。成功销账；失败仅记 code/unknown（INV-5）。"""
    tasks = []
    for code in codes:
        rng = effective.clamped_range(code)
        if rng is None:
            ctx.log(f"补齐跳过 {code}：不在有效清单")
            continue
        tasks.append((code, rng[0], rng[1]))
    if not tasks:
        ctx.log("补齐：无可拉代码")
        return True
    outcomes = run_backfill(pro, tasks, bars_dir,
                            bucket=bucket, progress=progress, cancel_check=cancel_check)
    ok = True
    for o in outcomes:
        if o.ok:
            store.clear_skip(o.code)
        else:
            ok = False
            if o.kind in (FailureKind.CODE, FailureKind.UNKNOWN):
                store.record_skip_failure(o.code, o.error, o.kind.value)
            ctx.log(f"补齐 {o.code} 失败（{o.kind.value if o.kind else '?'}）: {o.error}",
                    level="ERROR")
    ctx.log(f"补齐完成：{sum(1 for o in outcomes if o.ok)}/{len(outcomes)} 成功")
    return ok


def _tail_backfill(ctx, pro, store, effective, bars_dir, bucket, progress, cancel_check):
    """通道一：sync_skipped 非空即自动带一轮补齐；失败不翻转主作业终态（§3.7）。"""
    rows = store.skipped_rows()
    if not rows:
        return
    ctx.log(f"尾部补齐：重试 sync_skipped 中 {len(rows)} 只")
    if not _run_backfill_batch(ctx, pro, store, effective, [r["code"] for r in rows],
                               bars_dir, bucket, progress, cancel_check):
        ctx.log("尾部补齐存在失败（主作业终态不受影响）")


def backfill_codes_worker(ctx: JobContext, request: dict, now_cn: datetime | None = None) -> None:
    """通道二独立作业（job_type=market_backfill_codes，互斥集内）。"""
    from trendradar.infrastructure.runtime import storage_root

    root = storage_root()
    store = SyncStore(root)
    bars_dir = root / "market" / "bars"
    try:
        now_cn = now_cn or datetime.now(SHANGHAI)
        calendar = store.calendar_days()
        max_day = max(calendar) if calendar else None
        if max_day is None or max_day < now_cn.date():
            ctx.fail("官方日历未就绪/过期，拒绝补齐")
            return
        from trendradar.domain.market.sync.spec import latest_tradeable_day

        latest = latest_tradeable_day(calendar, now_cn)
        meta_file = root / "market" / "stock_meta.parquet"
        if meta_file.exists():
            meta = pl.read_parquet(meta_file)
        else:
            meta = sync_stock_list(bars_dir)
        effective = build_effective_list(meta, request.get("exclude_boards") or [], latest)
        codes = request.get("codes") or store.excluded_codes()
        bucket = TokenBucket()
        ok = _run_backfill_batch(
            ctx, get_pro(), store, effective, codes, bars_dir, bucket,
            lambda cur, total, msg: ctx.update_progress(cur, total, msg),
            lambda: ctx.check_cancelled(),
        )
        if ctx.check_cancelled():
            ctx.fail("Cancelled by user")
        elif ok:
            ctx.succeed({"kind": "market_backfill_codes", "codes": len(codes)})
        else:
            ctx.fail("补齐存在失败（明细见日志）")
    except Exception as e:
        ctx.fail(f"意外错误: {e}")
    finally:
        _invalidate_status_cache()
```

- [ ] **Step 5: 重写 `trendradar/app/services/market_service.py` 为薄门面**

整文件替换：

```python
"""市场数据同步门面：执行入口 + 确认入账同步 API（spec §3.7/§3.8/§6）。"""

from __future__ import annotations

from trendradar.app.jobs.executor import JobExecutor


def submit_market_bars_sync(executor: JobExecutor, request: dict) -> str:
    from trendradar.app.services.market_sync.service import bars_sync_worker

    return executor.submit(
        "market_bars_sync",
        lambda ctx: bars_sync_worker(ctx, request),
        request,
    )


def submit_market_backfill_codes(executor: JobExecutor, request: dict) -> str:
    """通道二"立即补齐"：先清 attempts（误判后重新给 3 次机会，spec §3.7）。"""
    from trendradar.app.services.market_sync.service import backfill_codes_worker
    from trendradar.infrastructure.runtime import storage_root
    from trendradar.infrastructure.storage.sync_store import SyncStore

    SyncStore(storage_root()).reset_skip_attempts()
    return executor.submit(
        "market_backfill_codes",
        lambda ctx: backfill_codes_worker(ctx, request),
        request,
    )


def confirm_doubtful_days() -> dict:
    """"确认入账"同步 API（非作业，spec §3.8）：all-or-nothing。"""
    from trendradar.app.services.market_sync.service import _invalidate_status_cache
    from trendradar.infrastructure.runtime import storage_root
    from trendradar.infrastructure.storage.sync_store import SyncStore
    from trendradar.infrastructure.tushare.writer import readback_calendar

    root = storage_root()
    store = SyncStore(root)
    doubtful = store.doubtful_days()
    if not doubtful:
        return {"status": "noop", "confirmed": []}
    readback = readback_calendar(root / "market" / "bars")
    missing = [d for d in doubtful if d not in readback]
    if missing:
        raise ValueError(
            f"以下日期不在盘上，请先重拉: {[d.isoformat() for d in missing]}"
        )
    store.add_done_days(doubtful)
    store.set_doubtful_days([])
    _invalidate_status_cache()
    return {"status": "ok", "confirmed": [d.isoformat() for d in doubtful]}
```

（旧 `submit_market_sync` / `_register_market_sync_metadata` / `get_market_status` 在本文件内整体删除；调用方改动在 Task 14 路由层完成，本任务结束时 `routes/market.py` 仍引用旧名 → 测试先只跑新文件。）

- [ ] **Step 6: 运行确认通过**

Run: `.venv/bin/pytest tests/app/test_market_sync_service.py -v`
Expected: PASS（16 个）

注意：此时 `tests/interfaces/test_api_contract.py::test_market_sync_force_passthrough` 等旧接口测试会因门面改名而红——预期内，Task 14 一并修复；本任务只要求新测试全绿且旧 `tests/infrastructure` / `tests/domain` 不新增红。

- [ ] **Step 7: 提交**

```bash
git add trendradar/app/services/market_sync/service.py trendradar/app/services/market_service.py tests/app/test_market_sync_service.py
git commit -m "feat: 新同步引擎编排层（两阶段/自检/换名/逃逸阀）+ 门面重写"
```

---

### Task 14: interfaces 层 —— schema/routes/presenters（R11/R15 + 面板数据源）

**Files:**
- Modify: `trendradar/interfaces/api/schemas/market.py`
- Modify: `trendradar/interfaces/api/routes/market.py`
- Modify: `trendradar/interfaces/api/presenters.py`
- Test: `tests/interfaces/test_api_contract.py`（改 + 增）

- [ ] **Step 1: 先改契约测试（失败）**

`tests/interfaces/test_api_contract.py` 中：

1) 删除 `test_market_sync_force_passthrough`，替换为：

```python
def test_market_bars_sync_submit_accepts_new_schema(client):
    resp = client.post(
        "/api/market-data/sync",
        json={"force": True, "exclude_boards": ["gem"], "accept_partial_baseline": True},
    )
    # token 缺失会让 worker 失败，但提交本身必须成功并给出 job_id
    assert resp.status_code == 200
    assert resp.json()["data"]["job_id"]


def test_market_backfill_submit(client):
    resp = client.post("/api/market-data/backfill", json={})
    assert resp.status_code == 200
    assert resp.json()["data"]["job_id"]


def test_confirm_doubtful_noop_when_empty(client):
    resp = client.post("/api/market-data/confirm-doubtful")
    assert resp.status_code == 200
    assert resp.json() == {"status": "noop", "confirmed": []}


def test_confirm_doubtful_rejects_days_not_on_disk(client):
    from trendradar.infrastructure.runtime import runtime_root
    from trendradar.infrastructure.storage.sync_store import SyncStore
    from datetime import date

    store = SyncStore(runtime_root() / "storage")
    store.set_doubtful_days([date(2026, 8, 26)])
    resp = client.post("/api/market-data/confirm-doubtful")
    assert resp.status_code == 400
    store2 = SyncStore(runtime_root() / "storage")
    assert store2.doubtful_days() == [date(2026, 8, 26)]        # 拒绝且不写账本


def test_confirm_doubtful_books_when_on_disk(client):
    import polars as pl
    from trendradar.infrastructure.runtime import runtime_root
    from trendradar.infrastructure.storage.sync_store import SyncStore
    from datetime import date

    d = date(2026, 8, 26)
    bars = runtime_root() / "storage" / "market" / "bars"
    bars.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"date": [d], "code": ["000001"], "open": [1.0], "high": [1.0],
                  "low": [1.0], "close": [1.0]}).write_parquet(bars / "000001.parquet")
    store = SyncStore(runtime_root() / "storage")
    store.set_doubtful_days([d])
    resp = client.post("/api/market-data/confirm-doubtful")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "confirmed": ["2026-08-26"]}
    store2 = SyncStore(runtime_root() / "storage")
    assert store2.done_days() == {d}
    assert store2.doubtful_days() == []


def test_status_payload_has_six_groups(client):
    resp = client.get("/api/market-data/status")
    assert resp.status_code == 200
    payload = resp.json()
    assert set(payload) >= {"calendar", "freshness", "bars_sync", "coverage",
                            "consistency", "storage"}
    assert set(payload["calendar"]) == {"status", "max_trade_date", "covers_today", "job_id"}
    assert set(payload["freshness"]) == {"trusted_through", "latest_tradeable",
                                         "stale_days", "total_missing_days"}
    assert set(payload["bars_sync"]) == {"status", "finished_at", "error_message", "job_id"}
    assert set(payload["coverage"]) == {"missing_codes", "skipped"}
    assert set(payload["consistency"]) == {"ledger_suspect", "marker_mismatch",
                                           "doubtful_days"}
    # 旧平铺字段全部收进 storage 组（原 test_market_status_shape 的断言落点）
    assert payload["storage"]["stock_count"] == 1
    assert payload["storage"]["local_file_count"] == 1
    assert payload["storage"]["latest_date"] == "2026-08-30"
    # 新鲜度基于读回实测：fixture 只种了 000001 一只到 2026-08-30
    assert payload["freshness"]["trusted_through"] == "2026-08-30"


def test_submit_execution_dispatch_market_bars_sync(client):
    resp = client.post(
        "/api/executions",
        json={"type": "market_bars_sync", "params": {"force": False}},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["execution_type"] == "market_bars_sync"
    assert payload["console_url"].startswith("/console/")
```

注意：既有 `test_market_status_shape`（约 L397-404，断言平铺字段）由上面的 `test_status_payload_has_six_groups` 取代——直接删除旧用例，不保留。

2) 运行：

Run: `.venv/bin/pytest tests/interfaces/test_api_contract.py -k "market or confirm or status_payload or dispatch_market" -v`
Expected: FAIL（404 / 字段缺失）

- [ ] **Step 2: 实现 schema**

`trendradar/interfaces/api/schemas/market.py` 中 `MarketSyncRequest` 整体替换：

```python
class MarketSyncRequest(BaseModel):
    force: bool = False
    exclude_boards: list[str] = Field(default_factory=list)
    accept_partial_baseline: bool = False
    codes: list[str] | None = None
```

（`TradingDatesResponse` 不动；前端幽灵字段删除在 Task 16。）

- [ ] **Step 3: 实现 routes**

`trendradar/interfaces/api/routes/market.py` 中 `/sync` 路由替换 + 新增两个路由：

```python
@router.post("/market-data/sync")
def submit_market_sync(body: MarketSyncRequest, request: FastAPIRequest):
    from trendradar.app.services.market_service import submit_market_bars_sync
    try:
        job_id = submit_market_bars_sync(
            _executor(request),
            {
                "force": body.force,
                "exclude_boards": body.exclude_boards,
                "accept_partial_baseline": body.accept_partial_baseline,
                "codes": body.codes,
            },
        )
        return {"data": {"job_id": job_id}}
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        import logging
        logging.getLogger("trendradar.api").error("submit_market_sync failed: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail="同步提交失败，请查看执行控制台日志")


@router.post("/market-data/backfill")
def submit_market_backfill(body: MarketSyncRequest, request: FastAPIRequest):
    from trendradar.app.services.market_service import submit_market_backfill_codes
    try:
        job_id = submit_market_backfill_codes(
            _executor(request),
            {"codes": body.codes, "exclude_boards": body.exclude_boards},
        )
        return {"data": {"job_id": job_id}}
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/market-data/confirm-doubtful")
def confirm_doubtful():
    from trendradar.app.services.market_service import confirm_doubtful_days
    try:
        return confirm_doubtful_days()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

- [ ] **Step 4: 实现 presenters**

`trendradar/interfaces/api/presenters.py` 三处改动：

1) 在 `market_status_payload` 之后追加缓存失效函数：

```python
def invalidate_market_status_cache() -> None:
    """任何同步终态/确认入账后调用（30 s TTL 对面板太慢）。"""
    _market_status_cache["at"] = 0.0
    _market_status_cache["payload"] = None
    _trading_dates_cache["at"] = 0.0
    _trading_dates_cache["payload"] = None
```

2) `_compute_market_status` 整体替换（旧平铺 5 字段 → 6 组，spec §4）：

```python
def _compute_market_status() -> dict:
    from datetime import datetime

    from trendradar.domain.market.sync.planner import stale_days
    from trendradar.domain.market.sync.spec import (
        BASELINE_START,
        SHANGHAI,
        latest_tradeable_day,
    )
    from trendradar.infrastructure.runtime import storage_root
    from trendradar.infrastructure.storage.sync_store import SyncStore
    from trendradar.infrastructure.tushare.writer import readback_calendar

    store_root = storage_root()
    bars_dir = store_root / "market" / "bars"
    meta_file = store_root / "market" / "stock_meta.parquet"
    sync = SyncStore(store_root)

    now_cn = datetime.now(SHANGHAI)
    today = now_cn.date()

    # ① 日历
    calendar_days = sync.calendar_days()
    max_cal = max(calendar_days) if calendar_days else None
    cal_job = _latest_job("market_calendar_sync")
    calendar = {
        "status": cal_job["status"] if cal_job else None,
        "max_trade_date": str(max_cal) if max_cal else None,
        "covers_today": bool(max_cal and max_cal >= today),
        "job_id": cal_job["job_id"] if cal_job else None,
    }

    # ② 新鲜度（基于读回实测日历，不信任 done_days）
    latest = latest_tradeable_day(calendar_days, now_cn) if calendar_days else None
    readback = readback_calendar(bars_dir) if bars_dir.is_dir() else set()
    trusted = max((d for d in readback if latest is None or d <= latest), default=None)
    freshness = {
        "trusted_through": str(trusted) if trusted else None,
        "latest_tradeable": str(latest) if latest else None,
        "stale_days": stale_days(calendar_days, readback, latest) if calendar_days else None,
        "total_missing_days": (
            len([d for d in calendar_days
                 if latest is not None and BASELINE_START <= d <= latest and d not in readback])
            if latest is not None else None
        ),
    }

    # ③ 最近一次行情同步
    bars_job = _latest_job("market_bars_sync")
    bars_sync = {
        "status": bars_job["status"] if bars_job else None,
        "finished_at": bars_job["finished_at"] if bars_job else None,
        "error_message": bars_job["error_message"] if bars_job else None,
        "job_id": bars_job["job_id"] if bars_job else None,
    }

    # ④ 个股覆盖（明细取前 10 条）
    skipped = sync.skipped_rows()
    coverage = {
        "missing_codes": len(skipped),
        "skipped": [
            {"code": r["code"], "attempts": r["attempts"], "last_error": r["last_error"]}
            for r in skipped[:10]
        ],
    }

    # ⑤ 一致性（marker_mismatch = 账本有勾但读回无此日）
    done_days = sync.done_days()
    consistency = {
        "ledger_suspect": sync.ledger_suspect(),
        "marker_mismatch": bool(done_days and done_days - readback),
        "doubtful_days": [d.isoformat() for d in sync.doubtful_days()],
    }

    # ⑥ 本地存储（沿用现状数字，供面板基线行）
    storage = {
        "data_dir": str(bars_dir),
        "stocklist": str(meta_file) if meta_file.exists() else "",
        "stock_count": len(list(bars_dir.glob("*.parquet"))) if bars_dir.is_dir() else 0,
        "local_file_count": len(list(bars_dir.glob("*.parquet"))) if bars_dir.is_dir() else 0,
        "latest_date": str(max(readback)) if readback else None,
    }

    return {
        "calendar": calendar,
        "freshness": freshness,
        "bars_sync": bars_sync,
        "coverage": coverage,
        "consistency": consistency,
        "storage": storage,
    }


def _latest_job(job_type: str) -> dict | None:
    import sqlite3

    db = _storage_root() / "app.db"
    if not db.exists():
        return None
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT job_id, status, finished_at, error_message FROM jobs "
            "WHERE job_type = ? ORDER BY id DESC LIMIT 1",
            (job_type,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None
```

3) `submit_execution_payload` 中 `market_data_sync` 分支整体替换为：

```python
    elif jtype == "market_bars_sync":
        from trendradar.app.services.market_service import submit_market_bars_sync
        job_id = submit_market_bars_sync(
            executor,
            {
                "force": params.get("force", False),
                "exclude_boards": params.get("exclude_boards") or [],
                "accept_partial_baseline": params.get("accept_partial_baseline", False),
            },
        )
        job_type = "market_bars_sync"
```

并删除文件顶部该函数内对 `submit_market_sync` 的 import（改为函数级导入如上）。

4) `result_url_for` 不需改（行情作业无结果页）。

- [ ] **Step 5: 运行确认通过**

Run: `.venv/bin/pytest tests/interfaces/test_api_contract.py -v`
Expected: PASS（新增 7 个 + 其余既有用例；旧 `test_market_sync_force_passthrough` 已删）

注意：`test_market_service_incremental.py` 等针对旧 `market_service.submit_market_sync` 的测试此时会红——属 Task 15 删除范围，本任务不处理。

- [ ] **Step 6: 提交**

```bash
git add trendradar/interfaces/api/schemas/market.py trendradar/interfaces/api/routes/market.py trendradar/interfaces/api/presenters.py tests/interfaces/test_api_contract.py
git commit -m "feat: 行情接口切换新引擎（新 schema/三路由/6 组状态字段）"
```

---

### Task 15: 清理旧同步代码与账本表（R12）

**Files:**
- Delete: `trendradar/infrastructure/tushare/syncer.py`、`trendradar/infrastructure/tushare/markers.py`
- Modify: `trendradar/infrastructure/tushare/calendar.py`（只留 `fetch_trade_calendar`）
- Modify: `trendradar/infrastructure/storage/schema.py`（删 `market_sync_runs` DDL + 索引）
- Modify: `trendradar/interfaces/api/app.py`（:70-71 注释措辞）
- Delete: `tests/infrastructure/test_markers.py`、`test_sync_plan.py`、`test_sync_planner.py`、`test_sync_by_stock.py`、`test_sync_market.py`、`test_sync_integration.py`、`test_sync_daily.py`、`test_tushare_syncer.py`、`tests/app/test_market_service_incremental.py`
- Modify: `tests/infrastructure/test_trade_calendar.py`（删 save/load 三用例，保留 `test_fetch_shards_by_year`）
- Modify: `tests/infrastructure/test_schema.py`（表集合更新）
- Modify: `tests/app/test_execution_registration.py`（market_sync 用例重写，R12）

**顺序强约束（spec §5）**：先删写入方/读取方及其测试断言 → 再删 `market_sync_runs` DDL。颠倒顺序中间态跑测试会崩。本任务分两批提交，天然满足。

- [ ] **Step 1: 删除旧模块与旧测试**

```bash
git rm trendradar/infrastructure/tushare/syncer.py \
       trendradar/infrastructure/tushare/markers.py \
       tests/infrastructure/test_markers.py \
       tests/infrastructure/test_sync_plan.py \
       tests/infrastructure/test_sync_planner.py \
       tests/infrastructure/test_sync_by_stock.py \
       tests/infrastructure/test_sync_market.py \
       tests/infrastructure/test_sync_integration.py \
       tests/infrastructure/test_sync_daily.py \
       tests/infrastructure/test_tushare_syncer.py \
       tests/app/test_market_service_incremental.py
```

- [ ] **Step 2: `calendar.py` 只留 fetch**

删除 `save_trade_calendar` / `load_trade_calendar` 两个函数与 `polars`/`Path` 中因此不再使用的导入；模块 docstring 改为：

```python
"""官方交易日历拉取（Tushare trade_cal）。

本地存储为 SQLite trade_calendar 表（SyncStore.insert_calendar_days，
INSERT OR IGNORE 只增不减，spec §3.3）；parquet 缓存方案已废弃。
"""
```

`tests/infrastructure/test_trade_calendar.py` 删除 `test_save_and_load_roundtrip` / `test_load_missing_returns_none` / `test_save_is_atomic` 三个用例，保留 `test_fetch_shards_by_year`。

- [ ] **Step 3: 验证旧引用清零**

Run: `grep -rn "syncer\|markers\|save_trade_calendar\|load_trade_calendar\|market_sync_runs" --include="*.py" trendradar/ tests/ | grep -v __pycache__`
Expected: 仅剩 `schema.py` 的 DDL（下一步删）与 `app.py` 注释（Step 4 改）

- [ ] **Step 4: 中间态回归**

Run: `.venv/bin/pytest tests/ -q`
Expected: 除 `test_schema.py::test_schema_creates_all_tables`（期望集含旧表）与 `test_execution_registration.py::test_market_sync_job_registers_execution_and_sync_run`（引用已删模块）外全绿。若还有其他红，属遗漏引用，先修掉再继续。

- [ ] **Step 5: 删 `market_sync_runs` DDL**

`trendradar/infrastructure/storage/schema.py`：删除 `CREATE TABLE IF NOT EXISTS market_sync_runs (...)` 整块与 `CREATE INDEX IF NOT EXISTS idx_market_sync_runs_execution_key ...` 一行。

`tests/infrastructure/test_schema.py` 的 `expected` 集合替换为：

```python
    expected = {
        "artifacts",
        "execution_items",
        "execution_links",
        "executions",
        "job_logs",
        "jobs",
        "strategy_group_members",
        "strategy_groups",
        "strategy_settings",
        "trade_calendar",
        "sync_done_days",
        "sync_meta",
        "sync_skipped",
    }
```

- [ ] **Step 6: 重写 `test_execution_registration.py` 的 market 用例**

删除 `test_market_sync_job_registers_execution_and_sync_run` 整函数，替换为：

```python
def test_market_sync_execution_registration(tmp_path, monkeypatch):
    """R12：新引擎只登记 executions 行（type=market_bars_sync），
    不再有 market_sync_runs 表。"""
    monkeypatch.setenv("TREND_RADAR_RUNTIME_ROOT", str(tmp_path))
    sc = _init_storage(tmp_path)

    from trendradar.app.services.market_sync.service import (
        register_market_sync_execution,
    )

    register_market_sync_execution("20260827_180000_market_bars_sync_abcd")

    conn = sc.connect()
    row = conn.execute(
        "SELECT * FROM executions WHERE execution_key = ?",
        ("20260827_180000_market_bars_sync_abcd",),
    ).fetchone()
    assert row is not None
    assert row["execution_type"] == "market_bars_sync"

    tables = {
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "market_sync_runs" not in tables
```

- [ ] **Step 7: `app.py` 注释措辞**

`trendradar/interfaces/api/app.py:70-71` 注释改为：

```python
    # Restart recovery: mark orphaned running jobs as failed so mutual
    # exclusion for market_bars_sync / market_backfill_codes is not
    # permanently locked.
```

- [ ] **Step 8: 全量回归**

Run: `.venv/bin/pytest tests/ -q`
Expected: 全绿（此时约 378 − 旧同步用例 + 新用例；数字只增不减为异常）

- [ ] **Step 9: 提交**

```bash
git add -A
git commit -m "chore: 清理旧同步引擎（syncer/markers/market_sync_runs）与遗留测试"
```

（`git add -A` 仅在本步骤使用：删除文件较多；提交前 `git status` 复核无误删。）

---

### Task 16: 前端 —— 入口（A 方案）与六行面板（spec §6）

**Files:**
- Modify: `frontend/src/types/marketData.ts`（6 组状态类型；删幽灵字段）
- Modify: `frontend/src/types/execution.ts`（联合类型改名）
- Modify: `frontend/src/services/marketData.ts`（+backfill / +confirmDoubtful）
- Modify: `frontend/src/pages/MarketData/MarketDataPage.tsx`（整体重写）

- [ ] **Step 1: 类型**

`frontend/src/types/marketData.ts` 整文件替换：

```ts
export interface TradingDatesResponse {
  from: string | null;
  to: string | null;
  count: number;
  dates: string[];
}

export interface CalendarStatus {
  status: string | null;
  max_trade_date: string | null;
  covers_today: boolean;
  job_id: string | null;
}

export interface FreshnessStatus {
  trusted_through: string | null;
  latest_tradeable: string | null;
  stale_days: number | null;
  total_missing_days: number | null;
}

export interface BarsSyncStatus {
  status: string | null;
  finished_at: string | null;
  error_message: string | null;
  job_id: string | null;
}

export interface SkippedCode {
  code: string;
  attempts: number;
  last_error: string | null;
}

export interface CoverageStatus {
  missing_codes: number;
  skipped: SkippedCode[];
}

export interface ConsistencyStatus {
  ledger_suspect: boolean;
  marker_mismatch: boolean;
  doubtful_days: string[];
}

export interface StorageStatus {
  data_dir: string;
  stocklist: string;
  stock_count: number;
  local_file_count: number;
  latest_date: string | null;
}

export interface MarketDataStatus {
  calendar: CalendarStatus;
  freshness: FreshnessStatus;
  bars_sync: BarsSyncStatus;
  coverage: CoverageStatus;
  consistency: ConsistencyStatus;
  storage: StorageStatus;
}
```

`frontend/src/types/execution.ts`：`ExecutionRequestType` 中 `"market_data_sync"` 改为 `"market_bars_sync"`。

- [ ] **Step 2: services**

`frontend/src/services/marketData.ts` 追加：

```ts
export interface JobSubmitResponse {
  data: { job_id: string };
}

export function submitMarketBackfill(): Promise<JobSubmitResponse> {
  return requestJson<JobSubmitResponse>("/api/market-data/backfill", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function confirmDoubtfulDays(): Promise<{ status: string; confirmed: string[] }> {
  return requestJson<{ status: string; confirmed: string[] }>(
    "/api/market-data/confirm-doubtful",
    { method: "POST" },
  );
}
```

- [ ] **Step 3: `MarketDataPage.tsx` 整体重写**

```tsx
import { DatabaseOutlined, PlayCircleOutlined, ReloadOutlined, ThunderboltOutlined } from "@ant-design/icons";
import {
  Alert, Button, Card, Checkbox, Col, Collapse, Modal, Row, Select, Space,
  Statistic, Tag, Typography, message,
} from "antd";
import { useEffect, useState } from "react";
import { submitExecution } from "../../services/executions";
import {
  confirmDoubtfulDays, getMarketDataStatus, submitMarketBackfill,
} from "../../services/marketData";
import type { MarketDataStatus } from "../../types/marketData";

const { Paragraph, Text, Title } = Typography;

const STATUS_TEXT: Record<string, string> = {
  success: "成功",
  failed: "失败",
  running: "运行中",
  queued: "排队中",
  cancelled: "已取消",
  cancelling: "取消中",
};

export function MarketDataPage() {
  const [messageApi, contextHolder] = message.useMessage();
  const [modal, modalContextHolder] = Modal.useModal();
  const [status, setStatus] = useState<MarketDataStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [backfilling, setBackfilling] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [excludeBoards, setExcludeBoards] = useState<string[]>([]);

  async function refreshStatus() {
    setLoading(true);
    try {
      const payload = await getMarketDataStatus();
      setStatus(payload);
      setErrorMessage("");
    } catch (error) {
      const text = error instanceof Error ? error.message : "加载行情状态失败。";
      setErrorMessage(text);
      messageApi.error(text);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refreshStatus();
  }, []);

  async function runSync(force: boolean, acceptPartialBaseline: boolean) {
    setSubmitting(true);
    try {
      const execution = await submitExecution({
        type: "market_bars_sync",
        params: {
          force,
          exclude_boards: excludeBoards,
          accept_partial_baseline: acceptPartialBaseline,
        },
      });
      window.location.href = execution.console_url;
    } catch (error) {
      const text = error instanceof Error ? error.message : "提交行情同步任务失败。";
      setErrorMessage(text);
      messageApi.error(text);
    } finally {
      setSubmitting(false);
    }
  }

  function confirmFullRebuild() {
    let acceptPartial = false;
    const missing = status?.coverage.missing_codes ?? 0;
    modal.confirm({
      title: "全量重建",
      content: (
        <div>
          <Paragraph>约 41 分钟 / 约 11,100 次调用 / 将覆盖现有数据。</Paragraph>
          {missing > 0 ? (
            <Checkbox onChange={(e) => { acceptPartial = e.target.checked; }}>
              存在 {missing} 只个股缺口，勾选后以带缺口方式入账
            </Checkbox>
          ) : null}
        </div>
      ),
      okText: "开始重建",
      cancelText: "取消",
      onOk: () => runSync(true, acceptPartial),
    });
  }

  async function runBackfill() {
    setBackfilling(true);
    try {
      const resp = await submitMarketBackfill();
      window.location.href = `/console/${resp.data.job_id}`;
    } catch (error) {
      const text = error instanceof Error ? error.message : "";
      // 后端 409 = 互斥拒绝（主同步运行中）
      messageApi.warning(text.includes("already running") ? "同步进行中，稍后再试" : (text || "提交补齐任务失败。"));
    } finally {
      setBackfilling(false);
    }
  }

  async function runConfirmDoubtful() {
    setConfirming(true);
    try {
      const resp = await confirmDoubtfulDays();
      messageApi.success(`已确认入账 ${resp.confirmed.length} 个交易日`);
      await refreshStatus();
    } catch (error) {
      const text = error instanceof Error ? error.message : "确认入账失败。";
      messageApi.error(text);
    } finally {
      setConfirming(false);
    }
  }

  const freshness = status?.freshness;
  const consistency = status?.consistency;
  const showWarningRow =
    !!consistency &&
    (consistency.ledger_suspect || consistency.marker_mismatch || consistency.doubtful_days.length > 0);

  return (
    <div className="feature-page">
      {contextHolder}
      {modalContextHolder}
      <section className="page-head">
        <div>
          <div className="eyebrow">Market Data</div>
          <Title level={1}>行情数据</Title>
          <Paragraph className="muted-text">查看本地行情状态，或提交日线数据同步任务。</Paragraph>
        </div>
      </section>

      {errorMessage ? <Alert className="workbench-alert" type="error" showIcon message={errorMessage} /> : null}

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <Row gutter={[12, 12]} className="workspace-metrics">
            <Col xs={12}>
              <Card className="metric-card" loading={loading}>
                <Statistic title="股票数" value={status?.storage.stock_count ?? 0} />
              </Card>
            </Col>
            <Col xs={12}>
              <Card className="metric-card" loading={loading}>
                <Statistic title="本地文件" value={status?.storage.local_file_count ?? 0} />
              </Card>
            </Col>
            <Col xs={12}>
              <Card className="metric-card" loading={loading}>
                <Statistic title="最新行情日期" value={status?.storage.latest_date ?? "-"} />
              </Card>
            </Col>
            <Col xs={12}>
              <Card className="metric-card" loading={loading}>
                <div className="metric-inline">
                  <Text className="metric-label">数据状态</Text>
                  <span className="metric-value">
                    {status?.storage.latest_date ? "已初始化" : "待初始化"}
                  </span>
                </div>
              </Card>
            </Col>
          </Row>

          <Card className="workbench-card" title="同步行情" style={{ marginTop: 12 }}>
            <Space direction="vertical" style={{ width: "100%" }}>
              <Button
                block type="primary" icon={<PlayCircleOutlined />}
                loading={submitting} onClick={() => runSync(false, false)}
              >
                补齐到最近可交易日
              </Button>
              <Collapse
                ghost
                items={[{
                  key: "advanced",
                  label: "高级",
                  children: (
                    <Space direction="vertical" style={{ width: "100%" }}>
                      <Select
                        style={{ width: "100%" }}
                        allowClear mode="multiple"
                        value={excludeBoards}
                        onChange={setExcludeBoards}
                        options={[
                          { label: "创业板 gem", value: "gem" },
                          { label: "科创板 star", value: "star" },
                        ]}
                        placeholder="排除板块（默认不排除；北交所永久剔除）"
                      />
                      <Button
                        block danger icon={<ThunderboltOutlined />}
                        loading={submitting} onClick={confirmFullRebuild}
                      >
                        全量重建
                      </Button>
                      <Text className="muted-text">
                        约 41 分钟 / 约 11,100 次调用 / 将覆盖现有数据
                      </Text>
                    </Space>
                  ),
                }]}
              />
            </Space>
          </Card>
        </Col>

        <Col xs={24} lg={10}>
          <Card
            className="workbench-card"
            loading={loading}
            title={
              <Space>
                <DatabaseOutlined />
                <span>数据地基</span>
              </Space>
            }
            extra={<Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={refreshStatus}>刷新</Button>}
          >
            <Space direction="vertical" style={{ width: "100%" }} size="middle">
              {/* ① 日历 */}
              <div>
                <Text strong>日历</Text>{" "}
                {status?.calendar.max_trade_date ? (
                  <>
                    <Text className="muted-text">覆盖至 {status.calendar.max_trade_date}</Text>{" "}
                    {status.calendar.status === "failed" ? (
                      <Tag color="red">
                        拉取失败{" "}
                        {status.calendar.job_id ? (
                          <a href={`/console/${status.calendar.job_id}`}>查看控制台</a>
                        ) : null}
                      </Tag>
                    ) : (
                      <Tag color={status.calendar.covers_today ? "green" : "orange"}>
                        {status.calendar.covers_today ? "正常" : "待刷新"}
                      </Tag>
                    )}
                  </>
                ) : (
                  <Text type="secondary">日历未就绪，行情同步已阻断</Text>
                )}
              </div>

              {/* ② 数据新鲜度 */}
              <div>
                <Text strong>数据新鲜度</Text>{" "}
                {freshness?.stale_days == null ? (
                  <Text type="secondary">未建库</Text>
                ) : freshness.stale_days === 0 ? (
                  <Tag color="green">已最新</Tag>
                ) : (
                  <Tag color="red">落后 {freshness.stale_days} 个交易日</Tag>
                )}
                {freshness?.latest_tradeable ? (
                  <div>
                    <Text className="muted-text" type="secondary">
                      最近可交易日 {freshness.latest_tradeable} · 可信边界{" "}
                      {freshness.trusted_through ?? "-"}
                      {freshness.total_missing_days != null &&
                        freshness.total_missing_days > (freshness.stale_days ?? 0) &&
                        ` · 总缺口 ${freshness.total_missing_days} 天`}
                    </Text>
                  </div>
                ) : null}
              </div>

              {/* ③ 最近一次行情同步 */}
              <div>
                <Text strong>最近一次行情同步</Text>{" "}
                {status?.bars_sync.status ? (
                  <>
                    <Text className="muted-text">{status.bars_sync.finished_at ?? "进行中"}</Text>{" "}
                    <Tag color={status.bars_sync.status === "success" ? "green" : "red"}>
                      {STATUS_TEXT[status.bars_sync.status] ?? status.bars_sync.status}
                    </Tag>
                    {status.bars_sync.job_id ? (
                      <a href={`/console/${status.bars_sync.job_id}`}>查看控制台</a>
                    ) : null}
                  </>
                ) : (
                  <Text type="secondary">暂无同步记录</Text>
                )}
              </div>

              {/* ④ 个股覆盖 */}
              <div>
                <Text strong>个股覆盖</Text>{" "}
                {(status?.coverage.missing_codes ?? 0) > 0 ? (
                  <>
                    <Tag color="red">缺 {status!.coverage.missing_codes} 只（连续失败）</Tag>
                    <Button
                      size="small" loading={backfilling} onClick={runBackfill}
                    >
                      立即补齐
                    </Button>
                  </>
                ) : (
                  <Text type="secondary">无缺口</Text>
                )}
              </div>

              {/* ⑤ 账本一致性（条件出现） */}
              {showWarningRow ? (
                <div>
                  <Text strong>⚠ 账本一致性</Text>
                  <div>
                    {consistency!.ledger_suspect ? (
                      <Text type="danger">
                        账本曾被判不可信，需手动全量重建
                        <Button size="small" danger style={{ marginLeft: 8 }} onClick={confirmFullRebuild}>
                          全量重建
                        </Button>
                      </Text>
                    ) : null}
                    {consistency!.marker_mismatch ? (
                      <div><Text type="danger">账本与文件不一致（读回对账失败）</Text></div>
                    ) : null}
                    {consistency!.doubtful_days.length > 0 ? (
                      <div>
                        <Text type="danger">
                          {consistency!.doubtful_days.length} 个交易日行数异常已跳过
                        </Text>
                        <Button
                          size="small" style={{ marginLeft: 8 }} loading={confirming}
                          onClick={runConfirmDoubtful}
                        >
                          确认入账
                        </Button>
                      </div>
                    ) : null}
                  </div>
                </div>
              ) : null}

              {/* ⑥ 基线 */}
              <div>
                <Text strong>基线</Text>{" "}
                <Text className="muted-text">
                  2015-01-01 起 · {status?.storage.stock_count ?? 0} 只 · 最新{" "}
                  {status?.storage.latest_date ?? "-"}
                </Text>
              </div>
            </Space>
          </Card>
        </Col>
      </Row>
    </div>
  );
}
```

（`dayjs` / `DatePicker` / `Form` / `formatPickerDate` 依赖全部随表单删除——P1 病根的两处 `dayjs("2019-01-01")` 一并消失。）

- [ ] **Step 4: 构建验证**

Run: `cd frontend && npm run build`
Expected: tsc + vite 均通过，无类型错误

- [ ] **Step 5: 提交**

```bash
git add frontend/src
git commit -m "feat: 行情页重做（补齐主按钮/全量重建二确认/六行面板）"
```

---

### Task 17: 全量回归 + 冒烟

**Files:** 无新增（只跑不改；发现回归则回到对应任务修）

- [ ] **Step 1: 后端全量测试**

Run: `.venv/bin/pytest tests/ -q`
Expected: 全绿

- [ ] **Step 2: 前端构建**

Run: `cd frontend && npm run build`
Expected: 通过

- [ ] **Step 3: uvicorn 冒烟（无 token 环境）**

Run:

```bash
TREND_RADAR_RUNTIME_ROOT=$(mktemp -d) .venv/bin/python -m uvicorn trendradar.interfaces.api.app:app --port 8123 &
sleep 2
curl -s localhost:8123/api/market-data/status | .venv/bin/python -m json.tool
curl -s -X POST localhost:8123/api/market-data/sync -H 'Content-Type: application/json' -d '{}' | .venv/bin/python -m json.tool
```

Expected: status 返回 6 组字段（空库灰态）；sync 返回 `data.job_id`（worker 随后因无 token failed，属预期）；`/api/market-data/trading-dates` 仍可用。冒烟后停掉进程。

- [ ] **Step 4: 对照 spec §7 逐条核账**

按 R1–R21 清单核对每条断言的落点（测试文件 + 用例名），确认无遗漏；把结果写入本计划末尾的核对表后提交：

```bash
git add docs/superpowers/plans/2026-09-01-market-sync-redesign-implementation.md
git commit -m "docs: 实施计划收尾（R1-R21 核对表）"
```

---

## R1–R21 落点核对表（Task 17 Step 4 填写）

| 编号 | 落点 | 状态 |
|---|---|---|
| R1 | tests/app/test_market_sync_service.py::test_r1_calendar_only_grows | 待填 |
| R2 | test_r2_stale_calendar_blocks | 待填 |
| R3 | test_r3_empty_calendar_fails_not_skip | 待填 |
| R4 | tests/domain/test_sync_planner.py（Task 2，11 用例） | 待填 |
| R5 | test_r5_incremental_abort_writes_nothing + Task 9 runner 测试 | 待填 |
| R6 | test_r6_incremental_doubtful_day_written_not_booked | 待填 |
| R7 | test_r7_full_env_breaker_records_nothing + Task 10 计数测试 | 待填 |
| R8 | test_r8_env_residue_rejects_partial_baseline / test_r8_no_confirm_keeps_staging | 待填 |
| R9 | test_r9_backfill_codes_never_touches_ledger | 待填 |
| R10 | test_r10_full_success_swaps_and_keeps_prev / test_r10_full_cancel_keeps_staging_bars_untouched | 待填 |
| R11 | tests/app/test_executor_mutex.py（Task 12）+ Task 14 契约测试 | 待填 |
| R12 | tests/infrastructure/test_schema.py + test_stocklist.py + test_execution_registration.py（Task 15） | 待填 |
| R13 | test_r13_full_doubtful_day_swapped_but_not_booked | 待填 |
| R14 | test_r14_doubtful_self_heals_next_round | 待填 |
| R15 | Task 14 confirm-doubtful 三契约用例 | 待填 |
| R16 | tests/infrastructure/test_stocklist.py + test_sync_selfcheck 分母用例（Task 3/7） | 待填 |
| R17 | test_r17_commit_failure_after_swap | 待填 |
| R18 | test_r18_uptodate_tail_backfill_env_failure_keeps_success | 待填 |
| R19 | Task 8 writer upsert 幂等用例（test_writer.py） | 待填 |
| R20 | test_r20_second_force_full_pulls_everything_again | 待填 |
| R21 | test_r21_full_staging_corruption_discards / test_r21_full_readback_missing_day_discards + test_r8_no_confirm_keeps_staging（保留分支） | 待填 |
