# 同步自检 doubtful v2（分段阈值 + suspend_d 对账）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **仓库约束（AGENTS.md）**：涉及子 Agent 必须先获用户明确授权——默认走 Inline Execution。

**Goal:** 断言①行数自检改为分年代阈值（≤2016: 0.75 / 2017-2018: 0.85 / ≥2019: 0.95），触发日改用 Tushare `suspend_d` 停牌清单精确对账自动裁决；行数统计分子与分母统一按 effective.codes 过滤（含北交所口径修复）。

**Architecture:** 纯判定（分段阈值 + 对账容差）落在 `domain/market/sync/selfcheck.py`（可独立测试）；停牌清单 IO 落在 `infrastructure/tushare/fetch.py`（`fetch_suspend_list`，含 200/min 节流与限频退避）；编排分别在 `runner.run_incremental`（增量）与 `service._run_full`（全量），通过后入账并写 `sync_meta.reconciled_days` 审计（每轮无条件覆写），失败维持现状 doubtful 路径（能力不降级）；对账窗口取消 → 中止换名/落账，终态 `cancelled`。

**Tech Stack:** Python 3.11 / polars / tushare pro API / pytest（hermetic，无网络）

**设计文档:** `docs/superpowers/specs/2026-09-09-sync-doubtful-v2-design.md`
**需求编号:** R18（分段阈值）/ R19（对账自动入账）/ R20（对账不可用回退 doubtful）/ R21（reconciled+doubtful 审计，每轮覆写）/ R22（行数口径按 effective.codes 过滤）/ R23（对账窗口取消 → 终态 cancelled，staging 保留）

**样本量纲约束（复审 N1/P5）**：对账容差 max(5, 2%×应成交) 的百分比项需
expected_traded > 250 才主导；abs=5 的下限只服务极小应市日。**全部对账正向/反向
用例的样本按 600 应市 / 300 停牌设计**（正向：容差 6、下界 294、actual 300；
反向：停牌清单空 → 下界 588、actual 300 < 588）——正反可判，无假绿。

**全量 fake 装置说明（复审 P2/P3 的根因，写用例前必读）**：
`code_days` 决定 staging 行数（→ actual 与断言①触发）；`suspend_codes` 只决定
对账分母 `suspended_alive`（→ 对账判定）。**两者互不影响**——造"触发日"必须让
某些股票当天没有 bar（从 code_days 剔除），只注 suspend_codes 不会触发。
`meta_codes` 决定 effective 清单规模（→ expected 分母与拉取任务集）；`code_days`
只对其中的码生效——**两者必须同时设置**（复审 R1：漏 meta_codes 会退回默认 3 只，
失败点在自检②而非对账，报错隔了三层）。

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

`tests/conftest.py` 追加（fixture 必须放 **tests 根 conftest**——`tests/infrastructure/`
与 `tests/app/` 两处都要用；勿建错到子目录 conftest）：

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


def test_fetch_suspend_list_cancelled_immediately(patching_sleep):
    # R23：cancel_check 命中 → 立即返回 cancelled，不发起接口调用
    class Pro:
        calls = 0

        def suspend_d(self, trade_date=None):
            Pro.calls += 1
            return pd.DataFrame({"ts_code": ["000001.SZ"]})

    pro = Pro()
    fr = fetch_suspend_list(pro, date(2015, 7, 8), pacing=0.0,
                            cancel_check=lambda: True)
    assert fr.kind is FailureKind.ENV
    assert fr.error == "cancelled"
    assert pro.calls == 0
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

（已知微小偏差：异常早返回路径不经过 pacing sleep——异常路径本就罕见，可接受。）

- [ ] **Step 2.5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_fetch.py -q`
Expected: PASS

- [ ] **Step 2.6: Commit**

```bash
git add trendradar/infrastructure/tushare/fetch.py tests/conftest.py tests/infrastructure/test_fetch.py
git commit -m "feat(sync): fetch_suspend_list 停牌清单拉取（节流+限频退避+取消，R19/R20/R23）"
```

---

### Task 3: runner — 增量路径接入对账 + 行数口径修复（R19/R20/R22）

**Files:**
- Modify: `trendradar/infrastructure/tushare/runner.py`
- Test: `tests/infrastructure/test_runner.py`

- [ ] **Step 3.1: `_eff` 改产出真实 codes + 对账用例样本升级（前置修复，复审 N1）**

对账容差 max(5, 2%×应成交) 的百分比项需 expected_traded > 250 才主导——样本 ≤21 只
时判定被 abs 容差吞掉（假绿）。`tests/infrastructure/test_runner.py` 的 `_eff`
（第 15-20 行附近）替换为：

```python
def _eff(expected_counts: dict[date, int]) -> EffectiveList:
    # 各日 expected 相同（测试场景均如此）：行数取其一，而非逐日累加。
    # 2026-09-09 v2：codes 必须为真实值——对账分母 suspended ∩ effective.codes
    # 依赖它；空 codes 会让 suspend 结果被交集清零（假绿，复审 N1/N4）。
    n = max(expected_counts.values())
    codes = tuple(f"{i:06d}" for i in range(n))
    rows = [(date(2010, 1, 1), None)] * n
    return EffectiveList(codes, rows, {})
```

（影响面：本文件全部既有用例。既有断言只依赖行数比值与 claimed/doubtful 集合，
codes 从空变真实后行为不变——doubtful 日走 reconcile 时因 DaySeqPro 无 `suspend_d`
属性 → ENV → 回退 doubtful，与原语义一致。）

- [ ] **Step 3.2: 写失败测试（正向对账 + 反向回退，600/300 样本）**

`tests/infrastructure/test_runner.py` 追加：

```python
def test_run_incremental_doubtful_reconciles_via_suspend_list():
    # R19：D2 300/600 = 0.5 < 0.95 触发，但 suspend_d 证实缺的 300 只停牌 → 自动入账。
    # 样本 600：百分比容差(6) 主导 abs(5)，反向用例才可区分（复审 N1）。
    pro = DaySeqPro({D1: 600, D2: 300, D3: 600})
    pro.suspend_d = lambda **kwargs: pd.DataFrame(
        [{"ts_code": f"{i:06d}.SZ"} for i in range(300, 600)])  # 缺的 300 只停牌
    result = run_incremental(pro, [D1, D2, D3], exclude_boards=None,
                             effective=_eff({D1: 600, D2: 600, D3: 600}))
    assert not result.aborted
    assert result.claimed_days == [D1, D2, D3]
    assert result.doubtful_days == []
    assert result.reconciled_days == [D2]


def test_run_incremental_doubtful_when_suspend_list_empty():
    # R20 反向：停牌清单为空 → 缺口 300 > 容差 12 → 仍 doubtful
    #（证明正向通过来自对账交集而非容差兜底；复审 N1 反证）
    pro = DaySeqPro({D1: 600, D2: 300, D3: 600})
    pro.suspend_d = lambda **kwargs: pd.DataFrame({"ts_code": []})
    result = run_incremental(pro, [D1, D2, D3], exclude_boards=None,
                             effective=_eff({D1: 600, D2: 600, D3: 600}))
    assert result.claimed_days == [D1, D3]
    assert result.doubtful_days == [D2]
    assert result.reconciled_days == []


def test_run_incremental_cancelled_during_reconcile_aborts_batch():
    # R23（增量）：对账窗口取消 → result.cancelled → 整批中止、一行不写。
    # cancel_check 计数：D1 拉取(1)、D2 拉取(2) 放行；D2 对账(3) 命中。
    pro = DaySeqPro({D1: 600, D2: 300, D3: 600})
    pro.suspend_d = lambda **kwargs: pd.DataFrame(
        [{"ts_code": f"{i:06d}.SZ"} for i in range(300, 600)])
    calls = {"n": 0}

    def cancel_check():
        calls["n"] += 1
        return calls["n"] > 2

    result = run_incremental(pro, [D1, D2, D3], exclude_boards=None,
                             effective=_eff({D1: 600, D2: 600, D3: 600}),
                             cancel_check=cancel_check)
    assert result.cancelled
    assert result.claimed_days == [D1]           # D2/D3 未入账
    assert result.doubtful_days == []            # 中止路径不落 doubtful（重跑自愈）
```

（三用例都显式传 `exclude_boards=None`——真实签名的必填位置参数，漏传即 TypeError。）

- [ ] **Step 3.3: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_runner.py -q -k "reconcil or suspend_list_empty"`
Expected: 三条全 FAIL（`IncrementalResult` 无 `reconciled_days`：正向对账用例与反向用例红在
`result.reconciled_days` 缺失；取消用例红在旧代码无对账直接落 doubtful →
`assert result.doubtful_days == []` 不成立）

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

`run_incremental` 签名增加 `suspend_pacing: float = 0.35`；
`effective_codes` 提到循环外（复审 N8）；判定段替换为
（**R22：分子按 effective.codes 过滤——Tushare `daily(trade_date=)` 现会返回
BJ 行，而分母（effective）剔 BJ，口径不统一会让 0.95 阈值的真实报警线被稀释**）：

```python
    effective_codes = list(effective.codes)
    for idx, day in enumerate(missing_days, start=1):
```

原逐日判定段：

```python
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
        if rfr.error == "cancelled":
            result.cancelled = True        # R23：对账窗口取消 → 终态 cancelled
            return result
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
先解引用会 TypeError——必须先判 `kind`（评审问题 5）。取消走 `result.cancelled`
复用既有中止语义：`_run_incremental` 的 `if res.cancelled: return False, None, True`
→ 终态 cancelled、一行不写。`pro` 无 `suspend_d` 属性时 `fetch_suspend_list`
内部 except 捕获 → ENV → 回退 doubtful，既有 doubtful 测试断言不变。）

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

- [ ] **Step 4.1: FakePro 支持停牌注水与 BJ 元数据（先改测试夹具）**

`tests/app/test_market_sync_service.py`：

① `FakePro.__init__` 追加：

```python
        self.suspend_codes = {}    # date -> 当日停牌代码列表（夹具自动补 .SZ 后缀）
        self.suspend_error = None  # 注水 suspend_d 异常（对账不可用）
```

`FakePro.stock_basic` 改用后缀：

```python
    def stock_basic(self, exchange="", list_status=None, fields=None):
        if list_status == "D":
            return FakeResp({})
        return _resp_meta(self.meta_codes)
```

`FakePro` 追加方法（**类名是 FakeResp**，复审 N6）：

```python
    def suspend_d(self, trade_date=None):
        d = date.fromisoformat(
            f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}")
        if self.suspend_error:
            raise Exception(self.suspend_error)
        return FakeResp({"ts_code": [f"{c}.SZ" for c in self.suspend_codes.get(d, [])]})
```

- [ ] **Step 4.2: 既有 doubtful 用例适配（复审 N2/N12 处置表 + P1 fixture 声明，漏一个 Step 4.8 必红）**

| 用例 | 场景 | fixture | 处置 |
|---|---|---|---|
| `test_r6_incremental_doubtful_day_written_not_booked`（原始 r6，d27 2/3） | 期望 doubtful | `patching_sleep` | 注水 `fake_pro.suspend_error = "频率超限"`（复审 N2：不注水会被小样本容差对账通过而翻红） |
| `test_r13_full_doubtful_day_swapped_but_not_booked`（08-26 2/3） | 期望 doubtful | `patching_sleep` | 同上注水 |
| `test_r17_full_doubtful_survives_commit_failure`（08-26 2/3） | 期望 doubtful | `patching_sleep` | 同上注水 |
| `test_r17_full_self_healed_doubtful_survives_commit_failure`（d26/d27 各 2/3） | 期望 doubtful | `patching_sleep` | 同上注水 |
| `test_r17_incremental_doubtful_survives_commit_failure`（d27 2/3） | 期望 doubtful | `patching_sleep` | 同上注水 |
| `test_r17_incremental_self_healed_doubtful_survives_commit_failure`（两日各 2/3） | 期望 doubtful | `patching_sleep` | 同上注水 |
| `test_r17_commit_failure_after_swap`（3 只全拉满 → ratio 1.0） | 不触发对账 | — | **无需改动** |
| `test_r21_full_staging_corruption_discards`（断言③提前 return） | 走不到对账循环 | — | **无需改动** |
| `test_full_doubtful_detail_recorded`（追加用例，08-26 1/3） | 期望 doubtful | `patching_sleep` | 同上注水 |
| `test_full_rebuild_exempts_booked_doubtful_days`（链前用例，签名追加并透传 fixture——见下） | 期望 doubtful | `patching_sleep` | 同上注水 |
| `test_incremental_doubtful_detail_recorded`（追加用例，d27 2/3） | 期望 doubtful | `patching_sleep` | 同上注水（保持小样本：本用例验证明细落库格式，非对账判定） |
| **阈值敏感**：`test_r7_resume_round_breaker_uses_full_batch_denominator` / `test_r7_success_clears_skip_and_unblocks_commit`（HEALTHY_CODES 21 只、出列后 20/21 = 0.952，距 0.95 线仅 0.0024） | 期望**不**触发 | 不需要 | 不改样本数，在 docstring 加注释"比值 0.952 距 2026 段阈值 0.95 仅 0.0024，改动行数统计口径须回归本用例" |

- [ ] **Step 4.3: 新增/改写用例（R19 正向/反向 + R20 回退，600 只样本越过量纲临界）**

```python
BIG = [f"{i:06d}" for i in range(600)]   # 600 应市：2% 容差(12) 主导 abs(5)，对账可判


def test_r6_incremental_suspension_reconciles_and_books(runtime, job_store,
                                                        sync_store, fake_pro):
    """R19：断言①触发但 suspend_d 对账一致 → 自动入账（reconciled 审计）。"""
    import json
    sync_store.insert_calendar_days(CAL)
    sync_store.add_done_days(DONE)
    d26, d27 = date(2026, 8, 26), date(2026, 8, 27)
    fake_pro.meta_codes = list(BIG)
    fake_pro.day_codes = {d26: BIG, d27: BIG[:300]}              # 27 日 300/600 → 触发
    fake_pro.suspend_codes = {d27: BIG[300:]}                    # 缺的 300 只全停牌

    ctx = run_worker(fake_pro, {}, job_store)

    assert ctx.status == "success"
    assert d27 in sync_store.done_days()
    assert d27 not in sync_store.doubtful_days()
    assert d27.isoformat() in json.loads(sync_store.get_meta("reconciled_days"))
    bars = runtime / "storage" / "market" / "bars"
    df = pl.read_parquet(bars / "000001.parquet")
    assert set(df["date"].to_list()) == {d26, d27}


def test_r6_reverse_insufficient_reconcile_stays_doubtful(runtime, job_store,
                                                          sync_store, fake_pro):
    """R20 反向：停牌清单为空 → 缺口 300 > 容差 12 → 仍 doubtful
    （证明正向通过来自对账交集而非 abs 容差兜底；复审 N1 反证）。"""
    sync_store.insert_calendar_days(CAL)
    sync_store.add_done_days(DONE)
    d26, d27 = date(2026, 8, 26), date(2026, 8, 27)
    fake_pro.meta_codes = list(BIG)
    fake_pro.day_codes = {d26: BIG, d27: BIG[:300]}
    fake_pro.suspend_codes = {d27: []}                           # 无停牌 → 应成交 600

    ctx = run_worker(fake_pro, {}, job_store)

    assert ctx.status == "failed"
    assert d27 not in sync_store.done_days()
    assert d27 in sync_store.doubtful_days()


def test_r6c_reconcile_unavailable_falls_back_doubtful(runtime, job_store,
                                                       sync_store, fake_pro,
                                                       patching_sleep):
    """R20：对账不可用 → 保守回退 doubtful（能力不降级）。"""
    sync_store.insert_calendar_days(CAL)
    sync_store.add_done_days(DONE)
    d26, d27 = date(2026, 8, 26), date(2026, 8, 27)
    fake_pro.meta_codes = list(BIG)
    fake_pro.day_codes = {d26: BIG, d27: BIG[:300]}
    fake_pro.suspend_error = "频率超限"

    ctx = run_worker(fake_pro, {}, job_store)

    assert ctx.status == "failed"
    assert "doubtful" in ctx.error
    assert d27 not in sync_store.done_days()
    assert d27 in sync_store.doubtful_days()
```

`test_r14_doubtful_self_heals_next_round` 首行改为链
`test_r6c_reconcile_unavailable_falls_back_doubtful`（fixture 透传：
`runtime, job_store, sync_store, fake_pro, patching_sleep` 全部透传；自愈：
第二轮 day_codes 全量 → 比值通过 → 入账），断言不变。

`test_incremental_doubtful_detail_recorded`：**保持小样本**（本用例验证明细落库
格式，非对账判定）+ `fake_pro.suspend_error = "频率超限"` + 签名追加
`patching_sleep`；精确断言不变（`{"actual": 2, "expected": 3, "ratio": 0.6667}`）。

链式用例签名同步（复审 Q2：被调函数加参后调用方必须透传，否则 TypeError）：

```python
def test_full_doubtful_detail_recorded(runtime, job_store, sync_store, fake_pro,
                                       patching_sleep):
    ...


def test_full_rebuild_exempts_booked_doubtful_days(runtime, job_store, sync_store,
                                                   fake_pro, patching_sleep):
    test_full_doubtful_detail_recorded(runtime, job_store, sync_store, fake_pro,
                                       patching_sleep)
```

同理，`test_r14` 链 `test_r6c_...` 时签名追加 `patching_sleep` 并透传。

- [ ] **Step 4.4: 全量路径用例（R19 全量正向含 BJ 断言 / R22 unit / R23 终态）**

**装置说明（复审 P2/P3 根因）**：`code_days` 决定 staging 行数（→ actual 与断言①
触发）；`suspend_codes` 只决定对账分母。造"触发日"必须让部分股票当天无 bar。

```python
def test_staging_day_rows_excludes_codes_outside_allowed(tmp_path):
    """R22 unit：_staging_day_rows 按 allowed_codes 过滤——BJ/清单外 bar 不计入 actual。"""
    from trendradar.app.services.market_sync.service import _staging_day_rows
    d = date(2026, 8, 26)
    for code in ("000001", "000002", "920001"):     # 920001 = BJ，不在 allowed
        pl.DataFrame({"code": [code], "date": [d], "close": [1.0]}).write_parquet(
            tmp_path / f"{code}.parquet")
    rows = _staging_day_rows(tmp_path, {"000001", "000002"})
    assert rows == {d: 2}                            # BJ 那行被过滤掉


def test_full_doubtful_reconciles_via_suspend_list(runtime, job_store,
                                                   sync_store, fake_pro,
                                                   patching_sleep):
    """R19 全量：触发日（08-26 有 100 只无 bar → 500/600 = 0.833 < 0.95 触发）
    对账一致（suspend 清单恰为缺的 100 只）→ 自动入账。样本 600 越过量纲临界。"""
    import json
    fake_pro.meta_codes = list(BIG)   # 复审 R1：漏设则 effective 退回默认 3 只，自检②即失败
    sync_store.insert_calendar_days(CAL)
    for c in BIG:
        fake_pro.code_days[c] = CAL[:4]
    for c in BIG[:100]:
        fake_pro.code_days[c] = [d for d in CAL[:4] if d != CAL[2]]  # 08-26 缺 100 只

    fake_pro.suspend_codes = {CAL[2]: BIG[:100]}     # 夹具自动补 .SZ

    ctx = run_worker(fake_pro, {"force": True}, job_store)

    assert ctx.status == "success"
    assert set(CAL[:4]) <= sync_store.done_days()
    reconciled = json.loads(sync_store.get_meta("reconciled_days"))
    assert date(2026, 8, 26).isoformat() in reconciled
```

```python
def test_full_cancelled_during_reconcile_keeps_staging(runtime, job_store,
                                                       sync_store, fake_pro,
                                                       monkeypatch):
    """R23：对账窗口取消 → ctx.cancel 于换名/落账之前；staging/bars/账本零改动。
    取消分支在 reconciliation_ok 之前短路，不受样本量纲约束（3 只小样本即可）。"""
    from trendradar.app.services.market_sync import service
    from trendradar.domain.market.sync.spec import FailureKind
    from trendradar.infrastructure.tushare.fetch import FetchResult
    sync_store.insert_calendar_days(CAL)
    bars = runtime / "storage" / "market" / "bars"
    bars.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"date": [date(2020, 1, 2)], "close": [1.0]}).write_parquet(
        bars / "OLD.parquet")
    d24, d25, d26, d27 = CAL[:4]
    fake_pro.code_days = {
        "000001": [d24, d25, d26, d27],
        "000002": [d24, d25, d26, d27],
        "600000": [d24, d25, d27],          # 08-26 无 bar → 该日 2/3 触发断言①
    }
    # 拉取期正常完成，进入对账窗口后首调即返回 cancelled
    monkeypatch.setattr(service, "fetch_suspend_list",
                        lambda *a, **k: FetchResult(None, FailureKind.ENV, "cancelled"))

    ctx = run_worker(fake_pro, {"force": True}, job_store)

    assert ctx.status == "cancelled"
    assert "对账窗口取消" in ctx.error        # 关键：证明走新分支而非既有 R10 路径
    assert (bars / "OLD.parquet").exists()     # 未换名
    assert not (runtime / "storage" / "market" / "bars_prev").exists()
    assert sync_store.done_days() == set()     # 账本零改动
    staged = sorted(p.stem for p in
                    (runtime / "storage" / "market" / "staging").glob("*.parquet"))
    assert staged == sorted(CODES)             # staging 原地保留（R23 验收点）
```

- [ ] **Step 4.5: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/app/test_market_sync_service.py -q -k "r6 or r14 or doubtful or full_cancelled or full_doubtful_reconciles or staging_day_rows"`
Expected: FAIL（service 尚未消费 `res.reconciled_days` / 未写 reconciled meta /
全量无对账接线 / `_staging_day_rows` 无过滤参数）

- [ ] **Step 4.6: service 增量路径实现**

`service.py`：

① 移除未使用导入 `doubtful_by_row_count`（评审问题 18）；导入区追加：

```python
from trendradar.infrastructure.tushare.fetch import fetch_suspend_list
```

selfcheck 导入块追加 `expected_trading_count` 与 `reconciliation_ok`。

② `_run_incremental` 在 `commit_incremental(store, sorted(claimed), merged_doubtful)`
之后插入（**R21：无条件覆写——复审 N7，不留上一轮陈旧值**；日志循环单独判空）：

```python
    store.set_meta("reconciled_days", json.dumps(
        [d.isoformat() for d in res.reconciled_days]))
    for day in res.reconciled_days:
        ctx.log(f"reconciled {day}：行数触发但 suspend_d 对账一致，自动入账")
```

（doubtful 分支保持 2cce2037 的明细/日期消息不变。）

- [ ] **Step 4.7: service 全量路径实现（R19/R21/R22/R23）**

`_run_full` 两处：

① 行数体检（现 `tripped = doubtful_detail(...)` 段）替换为——
分子过滤 + 逐触发日对账 + 取消即中止（换名/落账前）：

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
        # R23：对账窗口取消 → 中止换名/落账（staging 保留续传，与拉取期取消同契约）
        rfr = fetch_suspend_list(pro, r["day"], pacing=0.35,
                                 cancel_check=cancel_check)
        if rfr.error == "cancelled":
            ctx.cancel("Cancelled：对账窗口取消，未对账日未入账，重跑自愈")
            return
        alive = expected_trading_count(list(effective.rows), r["day"])
        suspended_alive = (len(set(rfr.df["code"].to_list()) & allowed_codes)
                           if rfr.kind is None else 0)
        if rfr.kind is None and reconciliation_ok(r["actual"], alive, suspended_alive):
            reconciled.append(r["day"])
        else:
            doubtful.append(r["day"])
            doubtful_detail_rows.append(r)
```

② 提交/终态段：

```python
    store.set_meta("reconciled_days", json.dumps(
        [d.isoformat() for d in reconciled]))
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

③ `_staging_day_rows` 增加过滤参数（service.py 底部既有实现）：

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

- [ ] **Step 4.8: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/app/test_market_sync_service.py -q`
Expected: 全部 PASS（600 只全量用例约 +3-5s；patching_sleep 屏蔽限频退避，套件总耗时预期 < 90s）

- [ ] **Step 4.9: Commit**

```bash
git add trendradar/app/services/market_sync/service.py tests/app/test_market_sync_service.py
git commit -m "feat(sync): 增量/全量 doubtful 触发日 suspend_d 对账接线 + reconciled 审计 + 行数口径剔 BJ（R19-R23）"
```

---

### Task 5: 全量回归 + 真实环境冒烟 + 推送

- [ ] **Step 5.1: 全量回归**

Run: `.venv/bin/python -m pytest -q`
Expected: 全部 PASS（无网络用例，hermetic；patching_sleep 保证限频退避不真实 sleep）

- [ ] **Step 5.2: 重启服务**

Run: `scripts/restart.sh`（docker-compose 部署，容器名 `trend-radar`）
Expected: 服务就绪

- [ ] **Step 5.3: 真实环境冒烟**

```bash
curl -s -X POST "localhost:${APP_PORT:-8818}/api/market-data/sync" \
     -H "Content-Type: application/json" -d '{}'
# 轮询 /api/market-data/status 至 bars_sync.status ∈ {success, failed}
```

Expected: success（无新缺口时不触发拉取也属正常）

- [ ] **Step 5.4: 推送**

```bash
git push origin feature
```

（凭据注入方式见会话记录：`git -c credential.helper=... push`，GH_TOKEN 走环境变量。）

---

## Self-Review（v2.3，吸收复审四 Q1-Q6 + 复审五 R1-R4）

- **P1**：patching_sleep 声明进**所有注水 suspend_error 的用例**（原始 r6 / r13 / r17 doubtful-survives ×4 / test_full_doubtful_detail_recorded / test_incremental_doubtful_detail_recorded / test_r6c_...，及链式调用它们的 test_r14、test_full_rebuild_exempts_...）；成功路径与 monkeypatch 用例不需要。fixture 确认放 tests 根 conftest。
- **P2**：全量正向用例触发条件修正——从 code_days 剔除当日 bar（suspend_codes 不影响 actual），500/600 = 0.833 < 0.95 真触发。
- **P3**：R22 拆独立 unit 用例 `test_staging_day_rows_excludes_codes_outside_allowed`（直接写 staging 文件验证过滤，复审 Q3 认可的准确覆盖方式）。
- **P4**：全量取消用例改 monkeypatch `service.fetch_suspend_list` 返回 cancelled（绕开 progress 计数装置），断言 `"对账窗口取消"` 文案区分既有 R10 路径。
- **P5**：全量正向用例升级 600 只（越过 et>250 量纲临界）。
- **P6**：恒真断言替换为 staging 文件名精确断言。
- **P7**：spec §7 R20 行改 `test_r6c_...` 并补 service 反向用例名；R19（全量）行更新样本描述；R22（unit）行补入。
- **P8**：`test_incremental_doubtful_detail_recorded` 统一为"小样本 + suspend_error"（验证明细格式，非对账判定），删除 BIG 指令冲突。
- **P9**：正向用例注释容差数字修正为 6。
- **P10**：suspend 注水统一带 `.SZ` 后缀。
- **R1**：全量正向用例补 `meta_codes = list(BIG)`（复审 R1：Q3 删 BJ 时连带误删，effective 退回 3 只致自检②失败）；全量用例的 meta_codes 行内注释补"漏设则 effective 退回默认 3 只"因果警示（紧贴易错点）。
- **R2**：恢复 `test_full_cancelled_during_reconcile_keeps_staging`（v2.2 修订时被连带误删，四处悬空引用）；补增量侧对称用例 `test_run_incremental_cancelled_during_reconcile_aborts_batch`（result.cancelled → 整批中止）。
- **R3**：spec §7 三行漂移同步（R19 全量样本描述、R22 收敛为 unit、R23 补 runner 取消用例）。
- **R4**：本节标题与内容随本轮更新；删除 orange×3 误输入。
- **机械化名称核查**：本文档引用的全部既有 test_* 用例名已逐个 grep 仓库确认存在（r6/r13/r14/r17×5/r21/r7×2/full×5/incremental detail×2）；新增用例名已标注新增。后续自查固定执行此步。补充：spec §7 每行括号内的场景描述，须与 plan 中对应用例的
docstring 逐条比对（名字对、描述错的情况本机制查不出——复审六 S1 教训）。
- **类型一致性**：`reconciled_days: list[date]` 三处一致；`fetch_suspend_list` 签名一致；`_staging_day_rows(dir, allowed_codes)` 定义与调用一致；FakePro.suspend_d 返回 ts_code（.SZ 后缀）与 fetch_suspend_list 的 [:6] 切片匹配。
- **回归面**（复审尾注）：`_eff` 真实 codes 波及 runner 全部用例（Step 3.1 已论证兼容）；FakePro.suspend_d 波及 service 全部 doubtful 用例（Step 4.2 表已逐用例注水/透传）。
