# 同步自检 doubtful v2（分段阈值 + suspend_d 对账）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **仓库约束（AGENTS.md）**：涉及子 Agent 必须先获用户明确授权——默认走 Inline Execution。

**Goal:** 断言①行数自检改为分年代阈值（≤2016: 0.75 / 2017-2018: 0.85 / ≥2019: 0.95），触发日改用 Tushare `suspend_d` 停牌清单精确对账自动裁决；行数统计分子与分母统一按 effective.codes 过滤（含北交所口径修复）。

**Architecture:** 纯判定（分段阈值 + 对账容差）落在 `domain/market/sync/selfcheck.py`（可独立测试）；停牌清单 IO 落在 `infrastructure/tushare/fetch.py`（`fetch_suspend_list`，含 200/min 节流与限频退避）；编排分别在 `runner.run_incremental`（增量）与 `service._run_full`（全量），通过后入账并写 `sync_meta.reconciled_days` 审计，失败维持现状 doubtful 路径（能力不降级）。

**Tech Stack:** Python 3.11 / polars / tushare pro API / pytest（hermetic，无网络）

**设计文档:** `docs/superpowers/specs/2026-09-09-sync-doubtful-v2-design.md`
**需求编号:** R18（分段阈值）/ R19（对账自动入账）/ R20（对账不可用回退 doubtful）/ R21（reconciled+doubtful 审计）/ R22（行数口径按 effective.codes 过滤）/ R23（对账窗口取消语义）

---

### Task 1: selfcheck — 分段阈值 + 对账判定纯函数

**Files:**
- Modify: `trendradar/domain/market/sync/selfcheck.py`
- Test: `tests/domain/test_sync_selfcheck.py`

- [ ] **Step 1.1: 写失败测试**

`tests/domain/test_sync_selfcheck.py` 追加：

```python
def test_threshold_for_bands():
    from trendradar.domain.market.sync.selfcheck import threshold_for
    assert threshold_for(date(2015, 7, 8)) == 0.75
    assert threshold_for(date(2016, 12, 30)) == 0.75
    assert threshold_for(date(2017, 6, 1)) == 0.85
    assert threshold_for(date(2018, 12, 31)) == 0.85
    assert threshold_for(date(2019, 1, 1)) == 0.95
    assert threshold_for(date(2026, 9, 9)) == 0.95


def test_doubtful_detail_uses_band_thresholds():
    eff = [(date(2010, 1, 1), None)] * 100
    # 2019 日 0.90：旧全局 0.75 通过、新分段 0.95 触发（R18）
    detail = doubtful_detail({date(2019, 6, 1): 90}, eff)
    assert [r["day"] for r in detail] == [date(2019, 6, 1)]
    # 2015 日 0.80：0.75 带内通过
    assert doubtful_detail({date(2015, 6, 1): 80}, eff) == []


def test_reconciliation_ok():
    from trendradar.domain.market.sync.selfcheck import reconciliation_ok
    # 真实案例：2015-07-08 应市 2781、停牌 1348、盘上 1446（+13 ≈ 0.9%）→ 通过（R19）
    assert reconciliation_ok(1446, expected_alive=2781, suspended=1348)
    # 多出方向无害（单边容差，只罚缺不罚多）
    assert reconciliation_ok(1500, expected_alive=2781, suspended=1348)
    # 缺口超容差：1400 < 1433 − max(5, 28.66)
    assert not reconciliation_ok(1400, expected_alive=2781, suspended=1348)
    # 小日子绝对容差 5：alive=100、停牌 1 → 应成交 99、容差 5
    assert reconciliation_ok(96, expected_alive=100, suspended=1)
    assert not reconciliation_ok(93, expected_alive=100, suspended=1)
```

- [ ] **Step 1.2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/domain/test_sync_selfcheck.py -q`
Expected: FAIL（`threshold_for` / `reconciliation_ok` 不存在）

- [ ] **Step 1.3: 实现**

`selfcheck.py` 在 `expected_trading_count` 之后追加。**注意：模块常量
`ROW_COUNT_RATIO = 0.75`（第 9 行）必须保留**——它是 <2017 段的阈值常量，且
`tests/domain/test_sync_selfcheck.py` 末尾有 `assert ROW_COUNT_RATIO == 0.75`：

```python
THRESHOLD_BANDS: tuple[tuple[int, float], ...] = (
    (2017, 0.85),   # 2017 ≤ year < 2019：重组停牌泛滥期（p1≈0.89-0.91）
    (2019, 0.95),   # year ≥ 2019：常态期（p1≈0.972-0.983）
)
DEFAULT_THRESHOLD = ROW_COUNT_RATIO  # 0.75：year < 2017（含 2015 股灾段）


def threshold_for(day: date) -> float:
    """断言①分段阈值：按被检日期所处年代取值（2026-09-09 spec v2 §D1/R18）。

    分年实测 p1：2015=0.520 / 2016=0.876 / 2017-2018≈0.89-0.91 / 2019+=0.972+；
    各段阈值取略低于该段 p1，留 2-4% 缓冲。
    """
    t = DEFAULT_THRESHOLD
    for start_year, v in THRESHOLD_BANDS:
        if day.year >= start_year:
            t = v
    return t


def reconciliation_ok(actual: int, expected_alive: int, suspended: int,
                      tol_pct: float = 0.02, tol_abs: int = 5) -> bool:
    """suspend_d 对账判定（spec v2 §D2/R19）：actual ≥ 应成交 − max(5, 2%×应成交)。

    单边容差：缺口方向（应成交却无 bar）才可能是拉取截断；多出方向是
    suspend_d 漏记/盘中复牌的真实 bar，无害。2% 经 2015-2019 全史 1219 天
    实测 100% 覆盖（2026-09-09）。
    """
    expected_traded = max(0, expected_alive - suspended)
    return actual >= expected_traded - max(tol_abs, tol_pct * expected_traded)
```

修改 `doubtful_detail` 与 `doubtful_by_row_count`：**删除 `threshold` 参数**，
逐日取 `threshold_for(d)`（detail 的键集保持 `day/actual/expected/ratio` 不变）：

```python
def doubtful_detail(
    day_rows: dict[date, int],
    effective: list[_EFFECTIVE_ROW],
    already_booked: set[date] | None = None,
) -> list[dict]:
    """断言①明细：低于当日分段阈值的日子（升序），含 actual/expected/ratio。

    already_booked：账本已有日豁免（2cce2037）；阈值按日分段（threshold_for）。
    """
    booked = already_booked or set()
    out = []
    for d in sorted(day_rows):
        if d in booked:
            continue
        n = day_rows[d]
        e = expected_trading_count(effective, d)
        if n < threshold_for(d) * e:
            out.append({"day": d, "actual": n, "expected": e,
                        "ratio": round(n / e, 4) if e else None})
    return out


def doubtful_by_row_count(
    day_rows: dict[date, int],
    effective: list[_EFFECTIVE_ROW],
    already_booked: set[date] | None = None,
) -> list[date]:
    """断言①：行数 < threshold_for(d) × expected(d) 的日期（升序）。"""
    return [r["day"] for r in doubtful_detail(day_rows, effective, already_booked)]
```

- [ ] **Step 1.4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/domain/test_sync_selfcheck.py -q`
Expected: 全部 PASS。**点名核查**：既有 `test_ratio_constant`（文件末尾
`assert ROW_COUNT_RATIO == 0.75`）必须仍 PASS——`ROW_COUNT_RATIO` 常量被保留为
<2017 段阈值；既有 `test_doubtful_by_row_count_075_threshold` 全用 2015 日期，
分段后仍 0.75，断言不变。

- [ ] **Step 1.5: Commit**

```bash
git add trendradar/domain/market/sync/selfcheck.py tests/domain/test_sync_selfcheck.py
git commit -m "feat(sync): 断言①分段阈值 threshold_for + suspend_d 对账判定 reconciliation_ok（R18/R19）"
```

---

### Task 2: fetch — `fetch_suspend_list` 停牌清单拉取

**Files:**
- Modify: `trendradar/infrastructure/tushare/fetch.py`
- Modify: `tests/conftest.py`（追加共享 fixture；该文件已存在）
- Test: `tests/infrastructure/test_fetch.py`（已存在，追加用例）

- [ ] **Step 2.1: 共享 fixture（R20：屏蔽限频退避的真实 sleep）**

`tests/conftest.py` 追加（fixture 放 tests 根 conftest——`tests/infrastructure/` 与
`tests/app/` 两处都要用；**勿建错到 tests/infrastructure/conftest.py**）：

```python
@pytest.fixture
def patching_sleep(monkeypatch):
    import trendradar.infrastructure.tushare.fetch as fetch_module
    monkeypatch.setattr(fetch_module.time, "sleep", lambda s: None)
```

（文件头需有 `import pytest`。）

- [ ] **Step 2.2: 写失败测试**

`tests/infrastructure/test_fetch.py` 追加（在既有 import 块中追加
`fetch_suspend_list`；响应直接用 pd.DataFrame，与文件内既有 fake 风格一致）：

```python
def test_fetch_suspend_list_collects_codes(patching_sleep):
    class Pro:
        def suspend_d(self, trade_date=None):
            return pd.DataFrame({"ts_code": ["000001.SZ", "600000.SH"]})

    fr = fetch_suspend_list(Pro(), date(2015, 7, 8), pacing=0.0)
    assert fr.kind is None
    assert fr.df["code"].to_list() == ["000001", "600000"]


def test_fetch_suspend_list_empty_response_gets_string_schema(patching_sleep):
    class Pro:
        def suspend_d(self, trade_date=None):
            return pd.DataFrame({"ts_code": []})

    fr = fetch_suspend_list(Pro(), date(2015, 7, 8), pacing=0.0)
    assert fr.kind is None
    assert fr.df["code"].dtype == pl.String        # Null dtype 会污染后续 concat
    assert fr.df.height == 0


def test_fetch_suspend_list_rate_limit_retries_then_env(patching_sleep):
    class Pro:
        calls = 0

        def suspend_d(self, trade_date=None):
            self.calls += 1
            raise Exception("抱歉，您访问接口(suspend_d)频率超限(200次/分钟)")

    pro = Pro()
    fr = fetch_suspend_list(pro, date(2015, 7, 8), pacing=0.0)
    assert fr.kind is FailureKind.ENV
    assert "suspend_d" in fr.error
    assert pro.calls == 3  # 限频退避重试耗尽（R20：调用方保守回退 doubtful）
```

- [ ] **Step 2.3: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_fetch.py -q -k suspend`
Expected: FAIL（`fetch_suspend_list` 不存在）

- [ ] **Step 2.4: 实现**

`fetch.py` 追加（文件已导入 `time` / `pl` / `FailureKind`）：

```python
def fetch_suspend_list(
    pro, day: date, pacing: float = 0.35, max_retries: int = 3, cancel_check=None,
) -> FetchResult:
    """suspend_d 当日停牌清单（code 集合），供断言①触发的精确对账（spec v2 §D2/D4）。

    接口限频 200 次/分钟（实测 0.11s/次）：pacing 串行节流；
    限频异常退避 62s 重试；耗尽/其它异常/取消 → ENV（调用方保守回退 doubtful，R20）。
    """
    day_s = day.strftime("%Y%m%d")
    last_error = ""
    for attempt in range(max_retries):
        if cancel_check and cancel_check():
            return FetchResult(None, FailureKind.ENV, "cancelled")
        try:
            resp = pro.suspend_d(trade_date=day_s)
            if resp is None:
                return FetchResult(None, FailureKind.UNKNOWN, "suspend_d 返回 None")
            df = resp if isinstance(resp, pl.DataFrame) else pl.DataFrame(
                resp.to_dict(orient="list"))
            codes = sorted({str(c)[:6] for c in df["ts_code"].to_list()}) if df.height else []
            if pacing:
                time.sleep(pacing)
            return FetchResult(
                pl.DataFrame({"code": codes}, schema={"code": pl.String}), None)
        except Exception as e:
            last_error = str(e)
            if "频率超限" in last_error and attempt < max_retries - 1:
                time.sleep(62)
                continue
            return FetchResult(None, FailureKind.ENV, f"suspend_d 对账失败: {last_error}")
    return FetchResult(None, FailureKind.ENV, f"suspend_d 重试耗尽: {last_error}")
```

（已知的微小偏差：异常早返回路径不经过 pacing sleep——异常路径本就罕见，可接受。）

- [ ] **Step 2.5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_fetch.py -q`
Expected: PASS

- [ ] **Step 2.6: Commit**

```bash
git add trendradar/infrastructure/tushare/fetch.py tests/conftest.py tests/infrastructure/test_fetch.py
git commit -m "feat(sync): fetch_suspend_list 停牌清单拉取（节流+限频退避，R19/R20）"
```

---

### Task 3: runner — 增量路径接入对账 + 行数口径修复（R19/R20/R22）

**Files:**
- Modify: `trendradar/infrastructure/tushare/runner.py`
- Test: `tests/infrastructure/test_runner.py`

- [ ] **Step 3.1: `_eff` 改产出真实 codes（前置修复，否则对账交集恒空 → 假绿）**

`tests/infrastructure/test_runner.py` 的 `_eff`（第 15-20 行附近）替换为：

```python
def _eff(expected_counts: dict[date, int]) -> EffectiveList:
    # 各日 expected 相同（测试场景均如此）：行数取其一，而非逐日累加。
    # 2026-09-09 v2：codes 必须为真实值——对账分母 suspended ∩ effective.codes
    # 依赖它；空 codes 会让 suspend 结果被交集清零（假绿）。
    n = max(expected_counts.values())
    codes = tuple(f"{i:06d}" for i in range(n))
    rows = [(date(2010, 1, 1), None)] * n
    return EffectiveList(codes, rows, {})
```

（影响面：本文件全部既有用例。既有断言只依赖行数比值与 claimed/doubtful 集合，
codes 从空变真实后行为不变——doubtful 日走 reconcile 时因 DaySeqPro 无 `suspend_d`
属性 → ENV → 回退 doubtful，与原语义一致。）

- [ ] **Step 3.2: 写失败测试（正向对账 + 反向回退 + 缺参数回归）**

`tests/infrastructure/test_runner.py` 追加：

```python
def test_run_incremental_doubtful_reconciles_via_suspend_list():
    # R19：D2 5/10 = 0.5 < 0.95 触发，但 suspend_d 证实缺的 5 只停牌 → 自动入账
    pro = DaySeqPro({D1: 10, D2: 5, D3: 10})
    pro.suspend_d = lambda **kwargs: pd.DataFrame(
        [{"ts_code": f"{i:06d}.SZ"} for i in range(5, 10)])  # 缺的 5 只停牌
    result = run_incremental(pro, [D1, D2, D3], exclude_boards=None,
                             effective=_eff({D1: 10, D2: 10, D3: 10}))
    assert not result.aborted
    assert result.claimed_days == [D1, D2, D3]
    assert result.doubtful_days == []
    assert result.reconciled_days == [D2]


def test_run_incremental_doubtful_when_suspend_list_empty():
    # R20 反向：停牌清单为空 → 缺口 5 > 容差 → 仍 doubtful（证明通过来自对账而非容差兜底）
    pro = DaySeqPro({D1: 10, D2: 2, D3: 10})   # 缺口 8，刻意远离 max(5, 2%) 边界
    pro.suspend_d = lambda **kwargs: pd.DataFrame({"ts_code": []})
    result = run_incremental(pro, [D1, D2, D3], exclude_boards=None,
                             effective=_eff({D1: 10, D2: 10, D3: 10}))
    assert result.claimed_days == [D1, D3]
    assert result.doubtful_days == [D2]
    assert result.reconciled_days == []
```

（两用例都显式传 `exclude_boards=None`——真实签名的必填位置参数，漏传即 TypeError。）

- [ ] **Step 3.3: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_runner.py -q -k reconciles`
Expected: FAIL（`IncrementalResult` 无 `reconciled_days`）

- [ ] **Step 3.4: 实现**

`runner.py`：

导入区改为：

```python
from trendradar.domain.market.sync.selfcheck import (
    doubtful_detail,
    expected_trading_count,
    reconciliation_ok,
)
```

并在从 fetch 的既有 import 列表里追加 `fetch_suspend_list`。

`IncrementalResult` 增加字段：

```python
    reconciled_days: list[date] = field(default_factory=list)
```

`run_incremental` 签名增加 `suspend_pacing: float = 0.35`；判定段替换为
（**R22：分子按 effective.codes 过滤——Tushare `daily(trade_date=)` 现会返回
BJ 行，而分母（effective）剔 BJ，口径不统一会让 0.95 阈值的真实报警线被稀释**）：

```python
        effective_codes = list(effective.codes)
        in_eff = (df.filter(pl.col("code").is_in(effective_codes)).height
                  if df.width > 0 else 0)
        detail = doubtful_detail({day: in_eff}, list(effective.rows))
        if not detail:
            result.claimed_days.append(day)
            continue
        # 断言①触发 → suspend_d 精确对账（spec v2 §D2）：一致则自动入账
        alive = expected_trading_count(list(effective.rows), day)
        rfr = fetch_suspend_list(pro, day, pacing=suspend_pacing,
                                 cancel_check=cancel_check)
        suspended_alive = (len(set(rfr.df["code"].to_list()) & set(effective.codes))
                           if rfr.kind is None else 0)
        if rfr.kind is None and reconciliation_ok(in_eff, alive, suspended_alive):
            result.claimed_days.append(day)
            result.reconciled_days.append(day)
            continue
        result.doubtful_days.extend(r["day"] for r in detail)
        result.doubtful_detail.extend(detail)
```

（`suspended_alive` 的三目写法是刻意的：`rfr.df` 在任何失败分支都是 `None`，
先解引用会 TypeError——必须先判 `kind`（评审问题 5）。`pro` 无 `suspend_d` 属性时
`fetch_suspend_list` 内部 except 捕获 → ENV → 回退 doubtful，既有 doubtful 测试
断言不变。）

- [ ] **Step 3.5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_runner.py -q`
Expected: 全部 PASS（含既有 doubtful 用例——fallback 行为兼容）

- [ ] **Step 3.6: Commit**

```bash
git add trendradar/infrastructure/tushare/runner.py tests/infrastructure/test_runner.py
git commit -m "feat(sync): 增量 doubtful 触发日 suspend_d 对账自动入账 + 行数口径剔 BJ（R19/R20/R22）"
```

---

### Task 4: service — 增量/全量路径接线 + 审计 + 既有用例适配

**Files:**
- Modify: `trendradar/app/services/market_sync/service.py`
- Test: `tests/app/test_market_sync_service.py`

- [ ] **Step 4.1: FakePro 支持停牌注水（先改测试夹具）**

`tests/app/test_market_sync_service.py`：`FakePro.__init__` 追加：

```python
        self.suspend_codes = {}    # date -> 当日停牌代码列表
        self.suspend_error = None  # 注水 suspend_d 异常（对账不可用）
```

`FakePro` 追加方法：

```python
    def suspend_d(self, trade_date=None):
        d = date.fromisoformat(
            f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}")
        if self.suspend_error:
            raise Exception(self.suspend_error)
        return _Resp({"ts_code": list(self.suspend_codes.get(d, []))})
```

- [ ] **Step 4.2: 既有 doubtful 用例适配（评审问题 9/12 清单，漏一个 Step 4.5 必红）**

v2 语义下这些用例的 doubtful 日会走 reconcile，需逐个处置：

| 用例 | 场景 | 处置 |
|---|---|---|
| `test_r13_full_doubtful_day_swapped_but_not_booked`（08-26 2/3） | 期望 doubtful | 注水 `fake_pro.suspend_error = "频率超限"`（对账不可用回退 doubtful） |
| `test_r17_*` 四个"doubtful survives commit failure"（同 08-26 2/3 场景） | 期望 doubtful | 同上注水 |
| `test_full_doubtful_detail_recorded`（追加用例，08-26 1/3） | 期望 doubtful | 同上注水 |
| `test_full_rebuild_exempts_booked_doubtful_days`（链前用例） | 同上 | 随前用例继承，无需单独改 |
| `test_incremental_doubtful_detail_recorded`（追加用例，d27 2/3） | 期望 doubtful | 同上注水 |
| **阈值敏感**：`test_r7_resume_round_breaker_uses_full_batch_denominator` / `test_r7_success_clears_skip_and_unblocks_commit`（HEALTHY_CODES 21 只、出列后 20/21 = 0.952，距 0.95 线仅 0.0024） | 期望**不**触发 | 不改样本数，在 docstring 加注释"比值 0.952 距 2026 段阈值 0.95 仅 0.0024，改动行数统计口径须回归本用例" |

（20/21 = 0.9524 > 0.95，当前断言成立；列为敏感注释而非改样本，最小扰动。）

- [ ] **Step 4.3: 新增/改写用例（R19 正向 + R20 回退）**

```python
def test_r6_incremental_suspension_reconciles_and_books(runtime, job_store,
                                                        sync_store, fake_pro):
    """R19：断言①触发但 suspend_d 对账一致 → 自动入账（reconciled 审计）。"""
    import json
    sync_store.insert_calendar_days(CAL)
    sync_store.add_done_days(DONE)
    d26, d27 = date(2026, 8, 26), date(2026, 8, 27)
    fake_pro.day_codes = {d26: CODES, d27: CODES[:2]}   # 27 日 2/3 → 触发
    fake_pro.suspend_codes = {d27: ["600000"]}          # 缺的正是停牌那只

    ctx = run_worker(fake_pro, {}, job_store)

    assert ctx.status == "success"
    assert d27 in sync_store.done_days()
    assert d27 not in sync_store.doubtful_days()
    assert d27.isoformat() in json.loads(sync_store.get_meta("reconciled_days"))
    bars = runtime / "storage" / "market" / "bars"
    df = pl.read_parquet(bars / "000001.parquet")
    assert set(df["date"].to_list()) == {d26, d27}


def test_r6b_reconcile_unavailable_falls_back_doubtful(runtime, job_store,
                                                       sync_store, fake_pro):
    """R20：对账不可用 → 保守回退 doubtful（能力不降级）。"""
    sync_store.insert_calendar_days(CAL)
    sync_store.add_done_days(DONE)
    d26, d27 = date(2026, 8, 26), date(2026, 8, 27)
    fake_pro.day_codes = {d26: CODES, d27: CODES[:2]}
    fake_pro.suspend_error = "频率超限"

    ctx = run_worker(fake_pro, {}, job_store)

    assert ctx.status == "failed"
    assert "doubtful" in ctx.error
    assert d27 not in sync_store.done_days()
    assert d27 in sync_store.doubtful_days()
```

`test_r14_doubtful_self_heals_next_round` 首行改为链
`test_r6b_reconcile_unavailable_falls_back_doubtful`（自愈：第二轮 day_codes 全量 →
比值通过 → 入账），断言不变。

`test_incremental_doubtful_detail_recorded`：在 `fake_pro.day_codes` 注水后加
`fake_pro.suspend_error = "频率超限"`（对账不可用才落 doubtful + detail）。

- [ ] **Step 4.4: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/app/test_market_sync_service.py -q -k "r6 or r14 or doubtful"`
Expected: FAIL（service 尚未消费 `res.reconciled_days` / 未写 reconciled meta）

- [ ] **Step 4.5: service 增量路径实现**

`service.py`：

① 移除未使用导入 `doubtful_by_row_count`（评审问题 18）；导入区追加
`fetch_suspend_list`（fetch 模块）与 selfcheck 的 `expected_trading_count`、
`reconciliation_ok`：

```python
from trendradar.infrastructure.tushare.fetch import fetch_suspend_list
```

selfcheck 导入块追加 `expected_trading_count` 与 `reconciliation_ok`。

② `_run_incremental` 在 `commit_incremental(store, sorted(claimed), merged_doubtful)`
之后、doubtful 判定之前插入：

```python
    if res.reconciled_days:
        store.set_meta("reconciled_days", json.dumps(
            [d.isoformat() for d in res.reconciled_days]))
        for day in res.reconciled_days:
            ctx.log(f"reconciled {day}：行数触发但 suspend_d 对账一致，自动入账")
```

（doubtful 分支保持 2cce2037 的明细/日期消息不变。）

- [ ] **Step 4.6: service 全量路径实现（R19/R21/R22/R23）**

`_run_full` 两处：

① 行数体检（现 `tripped = doubtful_detail(...)` 段）替换为——
分子过滤 + 逐触发日对账 + 取消语义：

```python
    allowed_codes = set(effective.codes)
    tripped = doubtful_detail(
        _staging_day_rows(staging_dir, allowed_codes), list(effective.rows),
        already_booked=store.done_days(),
    )
    reconciled: list[date] = []
    doubtful: list[date] = []
    doubtful_detail_rows: list[dict] = []
    for r in tripped:
        # 对账循环有界（分段后常态 7 天 ≈ 3.2s；限频退避最坏 21.7min），响应取消（R23）
        rfr = fetch_suspend_list(pro, r["day"], pacing=0.35,
                                 cancel_check=cancel_check)
        alive = expected_trading_count(list(effective.rows), r["day"])
        suspended_alive = (len(set(rfr.df["code"].to_list()) & allowed_codes)
                           if rfr.kind is None else 0)
        if rfr.kind is None and reconciliation_ok(r["actual"], alive, suspended_alive):
            reconciled.append(r["day"])
        else:
            doubtful.append(r["day"])
            doubtful_detail_rows.append(r)
    cancelled_during_reconcile = cancel_check and cancel_check()
```

提交/失败段（`commit_full` 之后）：

```python
    store.set_meta("reconciled_days", json.dumps(
        [d.isoformat() for d in reconciled]))
    if cancelled_during_reconcile:
        ctx.fail(f"已取消：{len(doubtful)} 日未完成对账未入账"
                 f"（数据已落盘，重跑自愈）")
        return
    if doubtful:
        _persist_doubtful_detail(store, doubtful_detail_rows)
        for r in doubtful_detail_rows:
            ctx.log(f"doubtful {r['day']} actual={r['actual']} "
                    f"expected={r['expected']} ratio={r['ratio']}", level="WARN")
        ctx.fail(f"{len(doubtful)} 个交易日行数异常（doubtful）："
                 f"{_fmt_days(doubtful)}，未入账，可确认入账；明细见控制台与"
                 f"sync_meta.doubtful_detail")
        return
    store.set_meta("doubtful_detail", "[]")
    ctx.succeed({"kind": "full", "fetched": len(tasks), "failed": len(failed)})
```

② `_staging_day_rows` 增加过滤参数（`service.py` 底部既有实现）：

```python
def _staging_day_rows(staging_dir: Path, allowed_codes: set[str]) -> dict:
    files = sorted(Path(staging_dir).glob("*.parquet"))
    if not files:
        return {}
    s = (
        pl.scan_parquet([str(p) for p in files])
        .filter(pl.col("code").is_in(sorted(allowed_codes)))   # R22：分子剔 BJ/清单外
        .group_by("date").agg(pl.len().alias("n")).collect()
    )
    return {row["date"]: row["n"] for row in s.iter_rows(named=True)}
```

- [ ] **Step 4.7: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/app/test_market_sync_service.py -q`
Expected: 全部 PASS

- [ ] **Step 4.8: Commit**

```bash
git add trendradar/app/services/market_sync/service.py tests/app/test_market_sync_service.py
git commit -m "feat(sync): 增量/全量 doubtful 触发日 suspend_d 对账接线 + reconciled 审计 + 行数口径剔 BJ（R19-R23）"
```

---

### Task 5: 全量回归 + 真实环境冒烟 + 推送

- [ ] **Step 5.1: 全量回归**

Run: `.venv/bin/python -m pytest -q`
Expected: 全部 PASS（无网络用例，hermetic）

- [ ] **Step 5.2: 重启生产服务**

Run: `hub restart gunark-app`（本会话实测的运行方式；docker-compose 部署环境用
`scripts/restart.sh`，容器名 `trend-radar`）
Expected: 服务就绪

- [ ] **Step 5.3: 真实环境冒烟**

```bash
curl -s -X POST localhost:8000/api/market-data/sync -H "Content-Type: application/json" -d '{}'
# 轮询 /api/market-data/status 至 bars_sync.status ∈ {success, failed}
```

（宿主机映射端口按部署实物：compose 环境为 `${APP_PORT:-8818}`；本会话环境 8000 直连。）
Expected: success（无新缺口时不触发拉取也属正常）

- [ ] **Step 5.4: 推送**

```bash
git push origin feature
```

（凭据注入方式见会话记录：`git -c credential.helper=... push`，GH_TOKEN 走环境变量。）

---

## Self-Review（v2 修订后）

- **评审 19 项处置对照**：#1→Task 3.4/4.6（分子过滤）+ §3.4；#2/#3→spec §3.2/§D1/§D4；#4→Task 3.1（_eff 真实 codes）+ 反向测试用缺口 8（远离 max(5,·) 边界）；#5→两处先判 `kind` 再解引用；#6→全文栅栏已核（成对）；#7→fixture 放 `tests/conftest.py`；#8→`exclude_boards=None`；#9→Step 4.2 清单（r13/r17×4 + 边界两用例）；#10→`cancel_check=cancel_check` + cancelled 终态 failed（R23）；#11→ROW_COUNT_RATIO 保留显式声明 + Step 1.4 点名；#12→敏感注释；#13→§D5/§6 仅落库声明；#14→spec §2 R18-R23 + §6/§7/§8/§9；#15→schema pl.String；#16→文档化偏差；#17→运维双轨；#18→移除未用导入；#19→§3.3 未验证面声明。
- **占位符扫描**：无 TBD/TODO/占位行；全部代码块为最终形态（Self-Review 结论已按 v2 修订更新）。
- **类型一致性**：`reconciled_days: list[date]`（runner 字段 = service 消费 = meta 序列化）；`fetch_suspend_list(pro, day, pacing, max_retries, cancel_check) -> FetchResult(df=pl.DataFrame{code:String})` 三处一致；`_staging_day_rows(dir, allowed_codes)` 定义与调用一致。
