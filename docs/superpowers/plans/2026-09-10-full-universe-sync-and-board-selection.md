# 全市场拉取（含北交所）与选股板块过滤 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 spec `2026-09-10-full-universe-sync-and-board-selection-design.md`（v3.2.1，commit ad757ac2）的 R24-R28：全量拉取覆盖北交所、涨跌停 920x 规则、选股板块过滤（boards）、文档/测试联动。

**Architecture:** 四层仓库（domain/app/infrastructure/interfaces）。R24 删 `build_effective_list` 的 `.BJ` 剔除；R25 涨跌停改用 `domain/market/sync/spec.is_bse_code` 单一实现源；R27 删同步服务 BSE 拒绝死分支；R26 请求契约加 `boards`（提交前校验 400，worker 内宇宙派生 boards∩codes 交集，无降级分支）；前端选股工作台加板块多选、列头勘误。数据回填（D5 backfill 348 只 BJ）与 A/B 对比是上线后运维动作，不在本计划内（记入 review-backlog）。

**Tech Stack:** Python 3.11 + Polars + FastAPI + pydantic；前端 Vite 6 + React 18 + antd 5 + TS。

**基线：** `pytest -q` = 605 passed（~130s，hermetic）。每个任务结束后除明确改写的用例外必须保持全绿。

---

### Task 1: R24 — effective list 纳入北交所

**Files:**
- Modify: `trendradar/infrastructure/tushare/stocklist.py:1,69,85,96-99`
- Test: `tests/infrastructure/test_stocklist.py:23-39`

- [ ] **Step 1: 改写三个用例（先看 RED）**

`tests/infrastructure/test_stocklist.py` 三处：

```python
def test_effective_list_includes_bse_codes():
    """R24：920x 与转板 833x/832x 精选层段均进 effective（不再按 .BJ 剔除）。"""
    eff = build_effective_list(META, exclude_boards=[], latest_tradeable=LATEST)
    assert "920099" in eff.codes
    assert "301999" not in eff.codes          # list_date > latest 仍剔
    assert set(eff.codes) == {"000001", "000004", "920099", "300001", "688001"}


def test_effective_list_exclude_boards_gem_star():
    eff = build_effective_list(META, exclude_boards=["gem", "star"], latest_tradeable=LATEST)
    assert set(eff.codes) == {"000001", "000004", "920099"}   # exclude_boards 无 bse 键，920099 回归


def test_effective_clamped_range():
    eff = build_effective_list(META, exclude_boards=[], latest_tradeable=LATEST)
    assert eff.clamped_range("000001") == (date(2015, 1, 1), LATEST)      # BASELINE 钳制
    assert eff.clamped_range("000004") == (date(2015, 1, 1), date(2026, 7, 13))  # 退市钳制
    assert eff.clamped_range("920099") == (date(2020, 7, 27), LATEST)     # list_date 钳制
```

（`test_effective_list_excludes_bj_and_future_listed` 更名并入第一条；META fixture 无需改——`920099.BJ` 已在其中。）

- [ ] **Step 2: 跑测试确认 RED**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_stocklist.py -q`
Expected: 3 failed（920099 被剔/钳制 None）

- [ ] **Step 3: 删除 .BJ 剔除并更新 docstring**

`stocklist.py` 行 98-99 删除：

```python
        if ts_code.endswith(".BJ"):
            continue  # Tushare daily 物理不提供北交所行情
```

行 69 docstring：
```python
    """有效清单 = L∪D − exclude_boards（与拉取侧同一过滤，§3.8 断言①分母）。"""
```
行 85 docstring：
```python
    """spec §3.6 待拉清单 ①-④：剔未来上市 / 剔排除板块 / 区间钳制（北交所随全市场拉取）。"""
```
行 1 模块 docstring 不含剔除字样，无需改。

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_stocklist.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add trendradar/infrastructure/tushare/stocklist.py tests/infrastructure/test_stocklist.py
git commit -m "feat(R24): effective list 纳入北交所——撤销 .BJ 剔除，转板股精选层段随全市场拉取"
```

---

### Task 2: R25 — 涨跌停 920x 走 is_bse_code 单一实现源

**Files:**
- Modify: `trendradar/domain/backtest/execution.py:1-23`
- Test: `tests/domain/test_backtest_execution.py:17-32`

- [ ] **Step 1: 加失败测试**

`tests/domain/test_backtest_execution.py` 的 `TestLimitPrices` 内追加（既有 `test_limit_up_price_bse` 保留 430001 老号段断言）：

```python
    def test_limit_up_price_bse_920(self):
        assert limit_up_price(10.0, code="920001") == pytest.approx(13.0)

    def test_min_trading_shares_bse_default_lot(self):
        from trendradar.domain.backtest.execution import min_trading_shares
        assert min_trading_shares(code="920001") == 100   # 北交所沿用默认档
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `.venv/bin/python -m pytest tests/domain/test_backtest_execution.py::TestLimitPrices::test_limit_up_price_bse_920 -q`
Expected: FAIL（现状 920001 → 11.0）

- [ ] **Step 3: 实现**

`execution.py` 头部加导入：

```python
from trendradar.domain.market.sync.spec import is_bse_code   # 北交所号段单一实现源（92/4/8）
```

`_limit_pct` 的 4/8 档替换为：

```python
def _limit_pct(*, code: str = "") -> float:
    # 2024-08 新规：风险警示股（ST/*ST）涨跌幅与普通股一致，无单独档位
    normalized = str(code or "").strip()
    if normalized.startswith(("300", "301", "688", "689")):
        return 0.20
    if is_bse_code(normalized):        # 北交所 ±30%（号段 92/4/8，见 spec.BSE_PREFIXES）
        return 0.30
    return 0.10
```

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `.venv/bin/python -m pytest tests/domain/test_backtest_execution.py -q`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add trendradar/domain/backtest/execution.py tests/domain/test_backtest_execution.py
git commit -m "fix(R25): 涨跌停 30% 档改用 is_bse_code——修复 920x 号段落 10% 档的系统性错误"
```

---

### Task 3: R27 — 同步服务删 BSE 拒绝死分支

**Files:**
- Modify: `trendradar/app/services/market_sync/service.py:54,513`、`trendradar/domain/market/sync/spec.py:17`、`trendradar/infrastructure/tushare/fetch.py:32`
- Test: `tests/app/test_market_sync_service.py:661-667`

- [ ] **Step 1: 反转用例 + fixture 注水（先看 RED）**

`tests/app/test_market_sync_service.py` 的 `test_explicit_backfill_rejects_bse_code`（:661-667）整体替换为：

```python
def test_explicit_backfill_accepts_bse_code(runtime, job_store, sync_store, fake_pro):
    """R24/R27：北交所可拉取——在册 920x 走常规补齐路径（空返回 OK_EMPTY、有数据落盘）。"""
    sync_store.insert_calendar_days(CAL)
    sync_store.add_done_days(DONE)
    fake_pro.meta_codes = list(CODES) + ["920001"]        # fixture 注水（复审 N6）
    fake_pro.code_days = {"920001": [date(2026, 8, 26)]}  # 精选层段有数据
    ctx = run_worker(fake_pro, {"codes": ["920001"]}, job_store)
    assert ctx.status == "success"
    assert (runtime / "storage" / "market" / "bars" / "920001.parquet").exists()
```

（R24 已进 effective → clamped_range 非空 → daily/adj 落盘；FakePro.adj_factor 镜像 daily 行集，不会触发 AdjFactorUnavailable。）

- [ ] **Step 2: 跑测试确认 RED**

Run: `.venv/bin/python -m pytest tests/app/test_market_sync_service.py::test_explicit_backfill_accepts_bse_code -q`
Expected: FAIL（现状 `ctx.status == "failed"`，error 含"北交所"）

- [ ] **Step 3: 删死分支**

`service.py` 行 54 删除：

```python
BSE_UNAVAILABLE_REASON = "北交所行情物理不可得（Tushare daily 不提供）"
```

行 513 三元塌缩：

```python
            reason = NOT_IN_LIST_REASON
```

同文件顶部删 `is_bse_code` 导入（grep 确认仅 :513 一处使用）。

`spec.py:17` docstring 更新：

```python
def is_bse_code(code: str) -> bool:
    """裸代码是否属北交所（号段 92/4/8，见 BSE_PREFIXES；行情已随全市场拉取）。"""
    return str(code).split(".")[0].zfill(6).startswith(BSE_PREFIXES)
```

`fetch.py:32` 注释更新：

```python
# 拉取排除仅支持创业板/科创板（无 bse 键：北交所随全市场拉取，见 2026-09-10 spec R27）
```

- [ ] **Step 4: 跑同步服务全量测试确认 GREEN**

Run: `.venv/bin/python -m pytest tests/app/test_market_sync_service.py tests/infrastructure/test_fetch.py tests/domain/test_sync_spec.py -q`
Expected: 全部 passed（`test_auto_backfill_skips_unfetchable_without_failing` 的 920001 残留项仍记 skipped、终态 success，不受影响；`test_explicit_backfill_partial_fails_but_keeps_what_it_fetched` 的 920001 改走 NOT_IN_LIST，仍 failed + 名字在 error，保持绿）

- [ ] **Step 5: Commit**

```bash
git add trendradar/app/services/market_sync/service.py trendradar/domain/market/sync/spec.py trendradar/infrastructure/tushare/fetch.py tests/app/test_market_sync_service.py
git commit -m "feat(R27): 删同步服务 BSE 拒绝死分支——920x 在册即补齐；backfill 用例语义反转 + fixture 注水"
```

---

### Task 4: R26 后端 — boards 契约、提交前校验、宇宙交集派生

**Files:**
- Modify: `trendradar/interfaces/api/schemas/execution.py:8-28`、`trendradar/app/services/selection_service.py:107-114,162-169`、`trendradar/interfaces/api/presenters.py:862,878-884,932-945`、`trendradar/interfaces/api/routes/backtest.py:151`
- Test: Create `tests/app/test_selection_boards.py`；Modify `tests/interfaces/test_api_contract.py`

- [ ] **Step 1: 写单元测试（先看 RED）**

Create `tests/app/test_selection_boards.py`：

```python
"""R26：boards 板块过滤——宇宙派生、交集语义、空宇宙日志、market 列探测。"""

from datetime import date, timedelta

import polars as pl
import pytest


def _make_ctx():
    class FakeCtx:
        def __init__(self):
            self.job_id = "boards-test"
            self.cancelled = False
            self.logs = []

        def log(self, message, level="INFO"):
            self.logs.append(message)

        def update_progress(self, current, total, message=""):
            pass

        def check_cancelled(self):
            return self.cancelled

    return FakeCtx()


def _meta_df():
    return pl.DataFrame({
        "code": ["000001", "600519", "920001", "300001"],
        "market": ["主板", "主板", "北交所", "创业板"],
    })


def _make_market(meta=None, bars_codes=("000001",)):
    """load_bars 收到的 codes 被记录 —— 宇宙派生结果的直接观测点。"""
    received = {}

    class FakeMarket:
        def trading_dates(self, start, end):
            d0, n = date(2026, 1, 1), 10
            return [d0 + timedelta(days=i) for i in range(n)]

        def stock_meta(self):
            return meta if meta is not None else _meta_df()

        def load_bars(self, codes, start, end, columns=None):
            received["codes"] = list(codes)
            rows = [{"code": c, "date": self.trading_dates(start, end)[0],
                     "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.2,
                     "volume": 1e6} for c in codes]
            return pl.DataFrame(rows)

    return FakeMarket(), received


def _store(tmp_path):
    from trendradar.infrastructure.storage.connection import StorageConnection
    from trendradar.infrastructure.storage.schema import init_schema
    sc = StorageConnection(tmp_path / "storage")
    sc.storage_root.mkdir(parents=True, exist_ok=True)
    init_schema(sc.connect())
    return sc


_REQ = {"start_date": "2026-01-01", "end_date": "2026-01-10",
        "strategies": ["big_bullish_volume"]}


def test_boards_filter_universe(tmp_path):
    from trendradar.app.services.selection_service import _run_selection
    from trendradar.domain.strategy.selectors import register_all
    register_all()
    market, received = _make_market()
    result = _run_selection(_make_ctx(), market, {**_REQ, "boards": ["北交所"]}, _store(tmp_path))
    assert received["codes"] == ["920001"]          # 宇宙 = market ∈ boards


def test_boards_plus_codes_intersection(tmp_path):
    from trendradar.app.services.selection_service import _run_selection
    from trendradar.domain.strategy.selectors import register_all
    register_all()
    market, received = _make_market()
    req = {**_REQ, "boards": ["主板", "北交所"], "codes": ["600519", "920001", "300001"]}
    _run_selection(_make_ctx(), market, req, _store(tmp_path))
    assert received["codes"] == ["600519", "920001"]   # 交集：300001 创业板被滤掉


def test_default_universe_unchanged(tmp_path):
    from trendradar.app.services.selection_service import _run_selection
    from trendradar.domain.strategy.selectors import register_all
    register_all()
    market, received = _make_market()
    _run_selection(_make_ctx(), market, dict(_REQ), _store(tmp_path))
    assert received["codes"] == ["000001", "300001", "600519", "920001"]  # 全宇宙、排序不变


def test_empty_universe_logs_board_reason(tmp_path):
    from trendradar.app.services.selection_service import _run_selection
    from trendradar.domain.strategy.selectors import register_all
    register_all()
    market, _ = _make_market()
    ctx = _make_ctx()
    _run_selection(ctx, market, {**_REQ, "boards": ["科创板"]}, _store(tmp_path))
    assert any("板块过滤后宇宙为空" in m and "科创板" in m for m in ctx.logs)


def test_validate_rejects_invalid_board(tmp_path):
    from trendradar.app.services.selection_service import validate_selection_request
    with pytest.raises(ValueError, match="未知板块"):
        validate_selection_request({"boards": [" Hack"]}, _store(tmp_path), _make_market()[0])


def test_validate_rejects_missing_market_column(tmp_path):
    from trendradar.app.services.selection_service import validate_selection_request
    no_market = _meta_df().drop("market")
    market, _ = _make_market(meta=no_market)
    with pytest.raises(ValueError, match="market"):
        validate_selection_request({"boards": ["北交所"]}, _store(tmp_path), market)
```

注意：`" Hack"`（带空格）同时验证非白名单值。`test_validate_rejects_missing_market_column` 用 drop 后的 meta——`stock_meta()` 降级场景的等价物。

- [ ] **Step 2: 跑测试确认 RED**

Run: `.venv/bin/python -m pytest tests/app/test_selection_boards.py -q`
Expected: 大面积 FAIL（boards 无处理：universe 测试收到全量 codes；validate 不认识 boards）

- [ ] **Step 3: 实现 validate_selection_request**

`selection_service.py` :107-114 替换为：

```python
VALID_BOARDS = ("主板", "创业板", "科创板", "北交所")


def validate_selection_request(request: dict, store, market_store=None) -> None:
    """提交时同步校验请求（group/strategy id、boards），未知 id/板块抛 ValueError → API 400。

    必须在 enqueue 之前调用：worker 里的 resolve 是静默跳过未知 id 的，等任务跑完
    才发现少了一个策略就太晚了。disabled 不算错误，仍由 resolve 阶段正常跳过。
    boards 校验同样只能在提交前做——worker 内 raise 只会作业 failed，HTTP 早已 202。
    market_store 由调用方传入（三个提交点均已握有）；缺列时 400，绝不静默放宽
    （否则用户点"北交所"会拿到全宇宙，结果看着正常但语义错误）。
    """
    group_defs, _member_defs, _settings_map = _get_strategy_resolve_input(store)
    validate_request_ids(group_defs, request)

    boards = request.get("boards")
    if not boards:
        return
    invalid = sorted({str(b) for b in boards} - set(VALID_BOARDS))
    if invalid:
        raise ValueError(f"未知板块: {invalid}（可选值：{list(VALID_BOARDS)}）")
    if market_store is None:
        raise ValueError("boards 过滤需要 market_store（调用方未传入）")
    meta = market_store.stock_meta()
    if "market" not in meta.columns:
        raise ValueError("stock_meta 缺少 market 列，无法按板块过滤（数据待全量重建后回填）")
```

- [ ] **Step 4: 实现 _run_selection 宇宙派生**

`selection_service.py` :162-169 替换为：

```python
    codes = request.get("codes")
    boards = request.get("boards") or []
    need_meta = not codes or bool(boards)
    meta = market_store.stock_meta() if need_meta else None
    if boards:
        # market 列存在性由提交前校验保证（缺列已在提交前 400），此处无降级分支
        meta = meta.filter(pl.col("market").is_in(boards))
    if codes:
        if boards:
            allowed = set(meta["code"].to_list()) if not meta.is_empty() else set()
            codes = sorted(c for c in codes if c in allowed)
            ctx.log(f"Boards {boards} ∩ whitelist: {len(codes)} stocks")
        else:
            ctx.log(f"Using {len(codes)} specified stocks")
    else:
        codes = meta["code"].to_list() if not meta.is_empty() else []
        if boards:
            ctx.log(f"Boards {boards}: universe {len(codes)} stocks")
        else:
            ctx.log(f"Using all {len(codes)} available stocks")
    if boards and not codes:
        ctx.log(f"板块过滤后宇宙为空（boards={boards}）——正常返回 0 信号")
```

（原 ：169 的 `codes = sorted(codes)` 已并入上方各分支。）

- [ ] **Step 5: schema 加 boards + 三处透传 + legacy 路由传 market_store**

`schemas/execution.py` — `ExecutionRequest` 与 `SelectionBacktestRequest` 均加一行：

```python
    boards: list[str] | None = None
```

`presenters.py` :862 顶层 key 循环元组加 `"boards"`：

```python
    for key in ("start_date", "end_date", "codes", "groups", "strategies", "boards"):
```

:878-884 `sel_params` 加一行：

```python
            "boards": params.get("boards"),
```

:932-945 `bt_params` 加两行（I4：codes 白名单一并补上）：

```python
            "codes": params.get("codes"),
            "boards": params.get("boards"),
```

两处 `validate_selection_request(sel_params, store)` / `(bt_params, store)` 均改为：

```python
        validate_selection_request(sel_params, store, market_store)
        validate_selection_request(bt_params, store, market_store)
```

`routes/backtest.py:151` 改为：

```python
        validate_selection_request(req, _store(request), _market_store(request))
```

- [ ] **Step 6: 跑单元测试确认 GREEN**

Run: `.venv/bin/python -m pytest tests/app/test_selection_boards.py -q`
Expected: 6 passed

- [ ] **Step 7: 加 API 契约测试**

`tests/interfaces/test_api_contract.py` 追加（沿用既有 400 测试风格）：

```python
def test_submit_selection_invalid_board_returns_400(client):
    """非法板块值提交时 400（校验在 enqueue 前），不是静默吞掉或作业失败。"""
    resp = client.post(
        "/api/executions",
        json={"type": "selection_single", "params": {"date": "2026-08-20", "boards": ["Hack"]}},
    )
    assert resp.status_code == 400
    assert "Hack" in resp.json()["detail"]


def test_selection_backtest_boards_passthrough(client, monkeypatch):
    """Critical 3 回归：selection_backtest 的 boards/codes 必须透传进请求（schema 不再吞）。"""
    captured = {}

    def fake_submit(executor, market_store, repo, request):
        captured.update(request)
        return "job-boards-1"

    import trendradar.app.services.backtest_service as bs
    monkeypatch.setattr(bs, "submit_selection_backtest", fake_submit)
    resp = client.post(
        "/api/executions",
        json={
            "type": "selection_backtest",
            "params": {
                "from": "2026-08-18", "to": "2026-08-20",
                "boards": ["北交所"], "codes": ["920001"],
                "strategies": ["bbi_kdj_b1"],
            },
        },
    )
    assert resp.status_code == 200
    assert captured["boards"] == ["北交所"]
    assert captured["codes"] == ["920001"]
```

（注意：monkeypatch 目标是 `backtest_service` 模块属性——presenters 在函数内 `from ... import submit_selection_backtest`，绑定发生在调用时。若实测绑定时机不同，改 patch `trendradar.interfaces.api.presenters` 命名空间；以跑通为准。）

客户端 fixture 的 stock_meta 无 market 列：非法值在板块值校验即 400，不触及列探测 → 两条用例均不依赖 fixture 改动。

- [ ] **Step 8: 跑 API 测试确认 GREEN**

Run: `.venv/bin/python -m pytest tests/interfaces/test_api_contract.py -q`
Expected: 全部 passed（既有 400/200 用例不受影响：不传 boards 时 `need_meta=False`，不触 stock_meta）

- [ ] **Step 9: Commit**

```bash
git add trendradar/interfaces/api/schemas/execution.py trendradar/app/services/selection_service.py trendradar/interfaces/api/presenters.py trendradar/interfaces/api/routes/backtest.py tests/app/test_selection_boards.py tests/interfaces/test_api_contract.py
git commit -m "feat(R26): 选股板块过滤——boards 契约 + 提交前校验 400 + boards∩codes 宇宙交集 + 空宇宙日志点名"
```

---

### Task 5: R26/R27 前端 — 板块多选、列头勘误、同步页文案

**Files:**
- Modify: `frontend/src/pages/Selections/SelectionWorkspacePage.tsx:22-27,145-155,217-293`
- Modify: `frontend/src/pages/Selections/SelectionResultPage.tsx:88`、`frontend/src/pages/Backtests/components/BacktestReportTables.tsx:116,161,187`
- Modify: `frontend/src/pages/MarketData/MarketDataPage.tsx:209`

- [ ] **Step 1: 表单值类型与 runSelection 注入**

`SelectionWorkspacePage.tsx` :22-27：

```typescript
interface SelectionFormValues {
  strategies?: string[];
  boards?: string[];
  date?: Dayjs;
  from?: Dayjs;
  to?: Dayjs;
}
```

`runSelection` 内 params 组装（strategies 行后）加：

```typescript
      if (values.boards?.length) {
        params.boards = values.boards;
      }
```

- [ ] **Step 2: 三个表单加板块多选**

文件顶部常量（组件外）：

```typescript
const BOARD_OPTIONS = ["主板", "创业板", "科创板", "北交所"].map((b) => ({ label: b, value: b }));
```

三处 `<Form.Item label="选股策略" …>` 之前各插入（latest/single/batch 共用）：

```tsx
<Form.Item label="板块" name="boards" initialValue={BOARD_OPTIONS.map((o) => o.value)}>
  <Select mode="multiple" options={BOARD_OPTIONS} allowClear
          placeholder="默认全选四板块；取消北交所可做 A/B 对比" />
</Form.Item>
```

（`allowClear` 清空即不传 boards = 全宇宙；`initialValue` 使默认全选——spec D4。）

- [ ] **Step 3: 列头勘误（复审 N5 定案）**

`SelectionResultPage.tsx:88` 与 `BacktestReportTables.tsx:116,161,187`：`title: "所属板块"` → `title: "所属行业"`。`dataIndex: "industry"` 全部保持不动。

- [ ] **Step 4: 同步页占位文案**

`MarketDataPage.tsx:209`：

```tsx
                        placeholder="排除板块（可选创业板/科创板；北交所随全市场拉取，不适用此选项）"
```

- [ ] **Step 5: 构建 + 类型检查**

Run: `cd frontend && npm run build`
Expected: tsc 无错误、vite build 成功

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/Selections/SelectionWorkspacePage.tsx frontend/src/pages/Selections/SelectionResultPage.tsx frontend/src/pages/Backtests/components/BacktestReportTables.tsx frontend/src/pages/MarketData/MarketDataPage.tsx
git commit -m "feat(R26/R27): 前端板块多选（默认全选）+ 所属行业列头勘误 + 同步页排除板块文案修正"
```

---

### Task 6: R28 — 文档联动与新基线

**Files:**
- Modify: `.superpowers/handoff.md:57`、`docs/superpowers/specs/2026-09-09-sync-doubtful-v2-design.md`（§3.4 追加注记）、`scripts/check_delisted_adj_factor.py:163`、`review-backlog.md`（追加新基线 + A/B 收尾项）

- [ ] **Step 1: handoff.md:57 涨跌停行更新**

```
- **A股规则**：T+1；涨跌停 主板 10% / 创业·科创 20%（300/301/688/689 前缀）/ 北交所 30%（92/4/8 开头，单一实现源 `domain/market/sync/spec.is_bse_code`）；**ST 无特殊档位**（2024-08 新规，`is_st` 已删除）；一手 100 股
```

- [ ] **Step 2: doubtful v2 spec §3.4 追加定性变更注记**

`docs/superpowers/specs/2026-09-09-sync-doubtful-v2-design.md` §3.4 末尾追加：

```markdown
> **2026-09-10 注记（R22 定性变更）**：全市场拉取 spec（2026-09-10）实施后，北交所已纳入
> effective，"BJ 口径污染"消失——本 spec 的 `effective.codes` 分子过滤由口径修复项
> **降级为纯防御**（防按日帧含清单外码）。
```

- [ ] **Step 3: 诊断脚本文案更新并重跑新基线**

`scripts/check_delisted_adj_factor.py:163` 打印行去"已剔北交所"：

```python
    print(f"最新可得交易日 {latest}；有效清单 {len(effective.codes)} 只"
          f"（排除板块/未来上市/退市早于 {BASELINE_START}）")
```

Run: `.venv/bin/python scripts/check_delisted_adj_factor.py --limit 0 --control 100`
Expected: 有效清单 ≈5818 只、退市样本 ≈257 只、熔断阈值 ≈290（与 spec §3.4 预告一致）——把实际输出记入下一步的 backlog 条目。

- [ ] **Step 4: review-backlog 追加两条**

`review-backlog.md` 末尾追加：

```markdown
## 2026-09-10 全市场拉取（BJ）实施收尾
- [ ] 新基线记录：check_delisted_adj_factor.py 重跑输出（有效清单 / 退市样本 / 熔断阈值）——实施 Task 6 后回填实际数字
- [ ] D5 数据回填（运维）：POST /api/market-data/backfill 显式传 348 只 BJ 代码（≈700 次调用 / +2.6 分钟），失败逐只点名
- [ ] D5 A/B 对比（收尾闸门）：同策略同窗口 boards 全 vs 排除北交所，记 trade_count/胜率/ΣPnL 入本文件——依据：920x 流通市值中位 8.6 亿，≥50 亿门槛族仅 12 只可能命中，形态族 picks 将实际改变
```

- [ ] **Step 5: Commit**

```bash
git add .superpowers/handoff.md docs/superpowers/specs/2026-09-09-sync-doubtful-v2-design.md scripts/check_delisted_adj_factor.py review-backlog.md
git commit -m "docs(R28): 文档联动——handoff 号段口径、doubtful v2 R22 定性注记、诊断脚本文案、backlog 收尾项"
```

---

### Task 7: 全量回归与收尾

- [ ] **Step 1: 全量测试**

Run: `.venv/bin/python -m pytest -q`
Expected: 605 + 新增 11（stocklist 改写净 +0、execution +2、market_sync 净 +0、boards +6、API +2）≈ 616 passed, 0 failed

- [ ] **Step 2: 对照 spec §5 逐条勾验收**

R24 用例 / R25 断言 / R26 六单测 + 两 API / R27 文案 / R28 文档——与 `docs/superpowers/specs/2026-09-10-full-universe-sync-and-board-selection-design.md` §5 逐条对齐，缺口当场补。

- [ ] **Step 3: Push**

```bash
git push origin feature
```

---

## Self-Review 记录

1. **Spec coverage**：R24→Task1、R25→Task2、R26→Task4+5、R27→Task3+5、R28→Task6；D3 校验落点（提交前 + market_store 参数）→Task4 Step3/5；空宇宙语义→Task4 Step4 + test_empty_universe_logs_board_reason；N6 fixture 注水→Task3 Step1；I4 bt_params 补 codes→Task4 Step5。D5 回填/A/B 为上线后运维 → Task6 记入 backlog（spec 定位即如此）。
2. **Placeholder scan**：无 TBD/TODO；所有代码步骤含完整代码；唯一不确定点（monkeypatch 绑定时机）已在文中写明两条路径与判据。
3. **Type consistency**：`boards: list[str] | None`（schema）/ `request.get("boards") or []`（service）/ `values.boards?: string[]`（前端）一致；`validate_selection_request(request, store, market_store=None)` 与三个调用点签名一致。
