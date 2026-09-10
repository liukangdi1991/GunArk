# 全市场拉取（含北交所）与选股板块过滤设计

> 版本：v3.1（2026-09-10）。**修订既有 spec**：取代 `2026-08-27-market-sync-redesign-design.md`
> R16 的"`.BJ` 股永不进入拉取清单与 expected"条款（该条款的立论"Tushare daily 物理不提供
> 北交所行情"已被实测证伪，详见 §3.1）。与 `2026-09-09-sync-doubtful-v2-design.md` 的联动
> 见 §4-D6。
> **实施顺序**：本 spec 评审通过后实施（doubtful v2 已落地 d19266c1）。

## §1 背景与问题

### 1.1 北交所数据的三方不一致（实测）

| 层 | 现状 |
|---|---|
| **清单** | `stock_meta` **5900** 行含 **348 只北交所**（`market` 列 = "北交所"） |
| **拉取（全量）** | `build_effective_list`（stocklist.py:98-99）无差别剔除 `.BJ` → 全量重建**从不拉** → 北交所无历史 |
| **拉取（增量）** | 按日 `daily(trade_date=)` 返回北交所行，且默认无板块过滤 → 行被写盘 |
| **盘上结果** | 343 个北交所文件，覆盖 2026-09-08 → 2026-09-10（**3 天**，增量持续追加中） |
| **消费（选股）** | 宇宙 = `stock_meta()` 全量（含北交所）→ 每轮进候选但 bars 不足 → 选不出，白读 343 个近乎空文件 |

### 1.2 旧假设已证伪

`stocklist.py:99`、`spec.py:17` 两处注释声称"Tushare daily 物理不提供北交所行情"。实测
（§3.1）：按股、按日、`adj_factor`、`daily_basic` **均已覆盖 920x 号段北交所**。

### 1.3 潜伏缺陷

- **涨跌停规则失效**：`domain/backtest/execution.py:_limit_pct` 的 30% 档按老号段
  `("4","8")` 判定；北交所 **920x** 新号段（当前 348 只中 345 只为 920x）→ 落进默认
  **10%** 档。北交所实际涨跌幅限制 **±30%**。当前因北交所选不出而未暴露；一旦纳入
  选股/回测，涨跌停判定与成交模拟将系统性错误。
- **选股无板块过滤**：宇宙固定为全市场；API 虽支持 `codes` 白名单但前端未暴露；
  前端无任何板块维度的选择入口。

### 1.4 设计方向（用户拍板）

**同步侧拉全（不做板块剔除）、选股侧按板块过滤**：职责分离——"拉取不用纠结剔除，
选股时选创业板/科创板/北交所/主板"。

## §2 需求

| 编号 | 需求 |
|---|---|
| R24 | 全量拉取覆盖北交所：撤销 `build_effective_list` 的 `.BJ` 剔除。345 只在市 920x 正常拉取；3 只转板股（833x/832x → 已转创业板/科创板）在精选层时期有数据、转板后无数据（走 `OK_EMPTY` 语义：不写文件、不计失败）；见 §8 已知限制——转板双代码口径 |
| R25 | 涨跌停规则覆盖 920x 北交所（±30%）：`_limit_pct` 改用 `is_bse_code` 单一实现源，消除号段漂移风险 |
| R26 | 选股板块过滤：请求新增 `boards`（主板/创业板/科创板/北交所）；默认不传 = 全宇宙（现状不变）；与 `codes` 白名单叠加为交集；非法板块值 400 |
| R27 | 同步侧一致性收敛：撤销"北交所永久剔除"硬编码；`exclude_boards` 机制保留（默认关闭）但**无 bse 键——上线后北交所无法经此排除**（前端占位文案需说明该选项只对创业板/科创板生效） |
| R28 | 文档与测试联动：修订 market-sync-redesign R16 记注；doubtful v2 §3.4 加联动注记；相关测试（`test_stocklist.py` ×3、`test_explicit_backfill_rejects_bse_code` 语义反转、`test_limit_up_price_bse` 扩展、5 处过期字面量）同步改写 |

（R1-R17 归属 market-sync-redesign spec；R18-R23 归属 doubtful v2 spec。）

## §3 实测依据（2026-09-10）

### 3.1 北交所数据可得性（Tushare 生产 token 实测）

| 接口/代码 | 结果 |
|---|---|
| `daily(ts_code="920077.BJ", 2024-01-01~)` | ✅ 652 行 |
| `daily(ts_code="920124.BJ" / "920943.BJ" / "920720.BJ", 2025-01-01~)` | ✅ 192 / 410 / 410 行（与各自上市时间吻合） |
| `adj_factor(ts_code="920077.BJ")` | ✅ 652 行（**关键**：全量重建第二路调用，缺则整只 strike） |
| `daily(trade_date=20260909)` | 5550 行，其中北交所 **343** 行 |
| `daily_basic(trade_date=20260909)`（选股流通市值） | ✅ 5550 行，其中北交所 343 行 |
| `daily(ts_code="920680.BJ", 全史)` / `adj_factor` | ✅ 928 / 1466 行（2026-01-05 退市） |
| `daily(ts_code="920305.BJ", 全史)` / `adj_factor` | ✅ 1296 / 1618 行（2026-07-30 退市） |
| `daily(ts_code="920077.BJ", 2021-08-31~2021-11-12)`（开市前窗口） | ✅ 47 行（与窗口交易日数吻合）；同窗 `adj_factor` ✅ 47 行 |
| `daily(ts_code="832317.BJ" / "833874.BJ" / "833994.BJ", 钳制区间)` | ✅ 299 / 311 / 309 行；`adj_factor` ✅ 424 / 479 / 484 行——**转板股精选层时期数据完整可得** |

### 3.2 北交所代码族与退市/转板构成

| 前缀 | 在市 920x | 2026 退市 920x | 2022 转板 833x/832x（.BJ 代码已无数据） |
|---|---|---|---|
| 920 | 343 | 2（920680 广道退、920305 云创退） | — |
| 833/832 | — | — | 3（832317→688287 已退、833874→301192、833994→301321） |
| **合计** | **343** | **2** | **3** |

### 3.3 `stock_meta.market` 列分布（板块过滤的数据基础；2026-09-10 09:59 快照）

| market | 行数 |
|---|---|
| 主板 | 3484 |
| 创业板 | **1448** |
| 科创板 | 619 |
| 北交所 | 348 |
| null | 1（`T600018`，2006 年退市的历史测试符号） |
| **合计** | **5900** |

### 3.4 代码前缀判板逻辑清查（全仓，含运维/交接文档）

| 位置 | 用途 | 处置 |
|---|---|---|
| `stocklist.py:98-99` `.endswith(".BJ")` + 注释 | effective 剔除 | **删除**（R24） |
| `stocklist.py:69,85` docstring | "L∪D − 北交所" 口径描述 | 更新（去北交所剔除字样） |
| `spec.py:13` `BSE_PREFIXES=("92","4","8")` | 裸码→ts_code 映射等 | **保留**（单一实现源，已含 92x） |
| `spec.py:17` `is_bse_code` docstring | "Tushare daily 物理不提供其行情" | 更新（过期假设） |
| `fetch.py:_to_ts_code` 走 `is_bse_code` | code→ts_code | 无需改（920x → `.BJ` 映射正确） |
| `execution.py:21` `("4","8")` → 0.30 | 涨跌停 | **改用 `is_bse_code`**（R25） |
| `execution.py:28` `("688","689")` → 200 股 | 最小买入单位 | 无需改（北交所 100 股 = 默认档） |
| `fetch.py:EXCLUDE_BOARD_PREFIXES`（dict） `{"gem","star"}` | 可选的拉取排除 | 保留（R27；默认关闭；**无 bse 键——上线后北交所无法经此排除**） |
| `service.py:54` `BSE_UNAVAILABLE_REASON` | 显式补齐的 BJ 拒绝理由 | **删除**（BJ 可拉后成死分支；`is_bse_code` 导入同步清理） |
| `service.py:513` `is_bse_code` 三元 | 补齐跳过理由 | 同上 |
| `scripts/check_delisted_adj_factor.py:161,163,173` | 运维诊断工具：调 `build_effective_list`、打印"已剔北交所"、算熔断阈值 | 更新文案 + 重跑（有效清单 5470→5818、退市样本 250→255、熔断阈值 273→290），新基线记入 review-backlog |
| `.superpowers/handoff.md:57` | "北交所 30%（4/8 开头）" | 更新为"92/4/8 开头，单一实现源 `spec.BSE_PREFIXES`" |
| 前端 MarketDataPage 占位文案 | "北交所永久剔除" | 更新（R27） |

## §4 核心设计

### D1 撤销 effective 的北交所剔除（R24）

`build_effective_list` 删除 `if ts_code.endswith(".BJ"): continue`（连同过期注释）。
生效面：

- **全量重建**：tasks 含 348 只北交所（比现状 +6.4% 只数）；`_to_ts_code` 已正确映射
  `920xxx → .BJ`；各只均可拉取（345 只在市 920x 正常返回、3 只转板股精选层时期数据
  完整可得——§3.1 实测 299~311 行）。
- **断言①分母**：`expected_trading_count` 随 effective 自然纳入北交所（上市日期起算）。
  **分母膨胀量级（按日实测）**：2020-07-27 起 +32 只（0.81%）→ 2021-11-12 起 +71 只
  （1.54%）→ 2023-06-30 起 +204 只（3.90%）→ 今日 +343 只（6.17%）。
  由分段阈值容差与 already_booked 豁免共同吸收（见 §8）。
- **增量路径**：本就含北交所行——纳入后两路口径统一，"全量剔、增量漏"的矛盾消失。
- **`service.py` 显式补齐路径**：`BSE_UNAVAILABLE_REASON` 拒绝分支删除后，BJ 代码走
  常规 `clamped_range` 校验（在册即补齐，不在册仍按 `NOT_IN_LIST_REASON` 拒绝）。

### D2 涨跌停规则改用单一实现源（R25）

```python
def _limit_pct(*, code: str = "") -> float:
    # 2024-08 新规：风险警示股（ST/*ST）涨跌幅与普通股一致，无单独档位
    normalized = str(code or "").strip()
    if normalized.startswith(("300", "301", "688", "689")):
        return 0.20
    if is_bse_code(normalized):        # ← 单一实现源（含 92/4/8 前缀）
        return 0.30
    return 0.10
```

`is_bse_code` 复用 `domain/market/sync/spec.BSE_PREFIXES`——本缺陷的根因即"同一概念两处
前缀表各自维护"，改用共享谓词后号段漂移不再可能。北交所无 ST 制度，风控注释不受影响。

### D3 选股板块过滤（R26）

**请求契约**（`ExecutionRequest` 与 `SelectionBacktestRequest` **均**增加字段，
`presenters` 三处透传点同步）：

```python
boards: list[str] | None = None   # 取值 ∈ {"主板","创业板","科创板","北交所"}；None/空 = 不滤
```

**透传点穷举**（`_run_selection` 有 3 个调用方）：

| 入口 | 请求类型 | 透传点 |
|---|---|---|
| POST `/api/executions` type=`selection_single`/`selection_latest` | ExecutionRequest | `presenters.py:862` 顶层 key 循环 + `:878-884` sel_params |
| POST `/api/executions` type=`selection_batch` | ExecutionRequest | 同上 |
| POST `/api/executions` type=`selection_backtest` + POST `/api/selection-backtest` | ExecutionRequest.params / SelectionBacktestRequest | `presenters.py:932-945` bt_params（需补 boards/codes 白名单——现状连 codes 都不在） |

pydantic 默认 `extra="ignore"`：不加字段则 boards 被**静默吞掉**（无 400、无日志）。

**语义**（与仓储/DB 原生对齐，值即 `stock_meta.market` 列字面量）：

```
无 boards 且无 codes → 全宇宙（现状不变，含 null market 的历史符号）
仅有 boards          → 宇宙 = { code | market(code) ∈ boards }
仅有 codes           → 白名单（现状不变）
boards + codes 同时  → 交集：白名单中 market ∈ boards 的子集
```

- **实现要点（复审 W6）**：`selection_service.py:162-169` 的 `meta` 仅在无白名单分支
  绑定。boards + codes 交集需重构为：

  ```python
  need_meta = (not codes) or boards
  meta = market_store.stock_meta() if need_meta else None
  if boards:
      meta = meta.filter(pl.col("market").is_in(boards)) if meta is not None else ...
  codes = ...  # 按分支派生
  ```

  仅在 if 分支加过滤会**静默丢弃交集语义**（白名单原样通过、无报错、无测试覆盖）。
- **校验**：`validate_selection_request` 增加 boards 取值校验，非法值 → `ValueError` → 400
  （与既有 group/strategy id 校验同风格）；boards 为空列表按"不传"处理。
- **null market 边界**：`T600018`（2006 退市）在任何板块过滤下都不命中（`market ∉ boards`）；
  该符号在任何现代选股区间均无 bar，无实际影响，但语义在本文档定死。
- **stock_meta 降级分支边界（复审 S14）**：`stock_meta()` 在 parquet 缺失/读取异常时回落
  扫描 bars 文件，仅返回 `code` 列（无 `market`）→ boards 过滤会抛 `ColumnNotFoundError`。
  处置：market 列缺失时**raise ValueError 走 400**（静默放宽会让用户点"北交所"却拿到
  全宇宙 5900 只——结果看着正常但语义错误）；R26 测试计划补
  `test_selection_boards_without_market_column`。
- **改动点（修正）**：单次/批量/选股+回测 **三个调用方**共用 `_run_selection`
  （selection_service.py:349 / :400 / backtest_service.py:429），宇宙派生逻辑只改一处。

### D4 前端（R26/R27）

1. **选股工作台**（`SelectionWorkspacePage`）：新增"板块"多选（主板/创业板/科创板/北交所，
   默认全选四板块），提交时随请求发送 `boards`。
2. **结果页**："所属板块"列修正——现状 `dataIndex: "industry"` 渲染的是**行业**（银行/
   软件服务等），并非板块。修正面（复审 I3）：`SelectionPick` 增加 `market` 字段
   （presenters 的 meta 查找一并读 `market` 列——`_enrich_stock_info` 与
   `_stock_meta_by_code` 两个助手同步），选股结果页"所属板块"列改绑 `market`；
   **BacktestReportTables.tsx** 三处同名列表头（:116/:161/:187）同步改绑 `market`。
3. **同步页**（`MarketDataPage`）：`exclude_boards` 选项保留；占位文案
   "排除板块（默认不排除；北交所永久剔除）" → "排除板块（可选创业板/科创板；
   北交所随全市场拉取，不适用此选项）"。

### D5 数据回填（运维步骤）

- **路径选择（复审 I2）**：不走全量重建（≈43 分钟 + 熔断 + 换名后未落账的回滚代价），
  走**显式补齐通道** `POST /api/market-data/backfill`（routes/market.py:57 →
  `_run_backfill_batch` → `run_backfill` → `upsert_code_file`）：
  - R24 后 BJ 已在 effective 内，`clamped_range` 非空 → 348 只 BJ 代码可显式传入
  - 直接 upsert 进 bars、INV-3 不碰日账本、不进全量自检②③④/换名/落账/
    accept_partial_baseline 闸门，失败逐只点名
  - 调用量：348 × 2 ≈ 700 次；TokenBucket(270/min) → **+2.6 分钟**
- 现有 343 个北交所文件**无需清理**——backfill 即覆盖（INV-4 幂等），且增量路径
  本就在持续追加。回填前北交所数据"只有两天"属已知过渡态。
- **选股侧影响**：宇宙只数不变（`stock_meta()` 全量 5900）；变的是 343 个文件由
  3 行涨到 200-1487 行——加载与 warmup 行数 **+2.4%**（bars 总行数 11.24M → 11.51M）。
- **转板股双代码口径（复审 I6）**：同一标的两段代码历史（.BJ 精选层段 + 新板块段）——
  选股/回测会将其视为两个独立标的；K 线/报告展示为两只票。§8 已知限制明写。

### D6 与既有 spec/计划的联动（R28）

| 文档 | 动作 |
|---|---|
| market-sync-redesign（2026-08-27） | R16 的".BJ 永不进入拉取清单与 expected"被本 spec 取代——在该 spec 的修订记录或本 spec 内留交叉引用（不改旧文正文，保持其评审溯源） |
| doubtful v2（2026-09-09，已实施 d19266c1） | §1/§3.4 的"BJ 口径污染"叙事在本 spec 实施后由"纳入统一"解决；**R22 的 `effective.codes` 分子过滤降级为纯防御**（防按日帧含清单外码——实施 doubtful v2 时它是口径修复项，本 spec 实施后口径撕裂消失）——实施本 spec 时在 doubtful v2 spec 追加该定性变更注记 |
| 本 spec 与 doubtful v2 的实施顺序 | doubtful v2 **已实施**（d19266c1）；本 spec 评审通过后实施，其数据回填依赖独立的运维步骤（backfill 348 只 BJ） |
| 测试联动 | 见 §5 R28 行（stocklist 断言反转×3、backfill 用例语义反转、`test_limit_up_price_bse` 扩展、5 处过期字面量清理） |
| 前端文案 | 见 D4 |

## §5 测试计划（R → 用例映射）

| 需求 | 用例 |
|---|---|
| R24 | `test_effective_list_includes_bse_codes`（920x 在 effective；3 只转板 833x/832x 钳制区间非空 → 同样进 codes/tasks，正常拉取精选层数据）；既有 BJ 排除用例改写（见 R28） |
| R25 | 扩展既有 `test_backtest_execution.py::test_limit_up_price_bse`（430001 老号段保留）+ 新增 920x 断言（`limit_up_price(10.0, code="920001") == 13.0`）；+ `min_trading_shares(code="920001") == 100`（钉住默认档）；创业板/科创板 20% 与默认 10% 既有断言保持绿 |
| R26 | `test_selection_boards_filter_universe`（验收断言 = picks 非空 且 pick.code 全 ∈ 当日 stock_meta 中 market=='北交所' 的在册只数集合，不硬编码 348）；`test_selection_boards_plus_codes_intersection`；`test_selection_invalid_board_400`；`test_selection_default_universe_unchanged`（不传 boards 行为不变）；`test_selection_boards_without_market_column`（降级分支：raise 400） |
| R26（选股回测入口） | `test_selection_backtest_boards_filter`（经 type=`selection_backtest` 提交含 boards 的请求 → 宇宙按板块过滤——否则 Critical 3 修复无法被证伪）；`test_selection_backtest_boards_codes_intersection` |
| R27 | 前端文案人工走查；后端 `exclude_boards` 既有用例保持绿（R28 的用例改写对齐——见下方三条） |
| R28 | ① `test_stocklist.py::test_effective_list_excludes_bj_and_future_listed` 改写（断言集改为 {000001,000004,300001,688001,920099}）；② `test_stocklist.py::test_effective_clamped_range` 改写（`clamped_range("920099") == (date(2020,7,27), LATEST)`）；③ `test_stocklist.py::test_effective_list_exclude_boards_gem_star` 改写（断言集加回 920099——**exclude_boards 无 bse 键，此用例名下 R24 后 920099 回归**）；④ `test_explicit_backfill_rejects_bse_code` 语义反转并更名 `test_explicit_backfill_accepts_bse_code`；⑤ `test_backfill_*` 两用例的 "920001" 换 "999999"（**可选语义清理**：R24 后走 NOT_IN_LIST_REASON 仍 failed + 名字在 error，不会翻红——标注为可选而非必改）；⑥ 过期字面量清理；⑦ 全量回归 |

## §6 前端影响

- 选股工作台：新增板块多选；提交 payload 增字段。
- 选股回测工作台：**不加**板块控件（复审 C2 裁定按 §7：后端 schema+透传补齐，前端不加
  ——该入口以策略+日期为核心，板块需求由选股工作台承接；但后端 boards/codes 透传
  已补齐，API 层面可用）。
- 结果页："所属板块"列改绑 `market`，行业另立（或标题改"所属行业"）；BacktestReportTables
  三处同步。
- 同步页：仅文案修正。
- 其余页面（K 线、行情数据）：零变更。北交所 K 线在 backfill 后自然可看（当前仅 3 根）。

## §7 YAGNI

- **不改 `exclude_boards` 机制**（保留但不默认启用；删除它属 API 契约变更，无功能收益）。
  **已知限制**：`EXCLUDE_BOARD_PREFIXES` 无 bse 键 → 上线后北交所**无法通过
  exclude_boards 排除**；前端选项只对创业板/科创板生效，占位文案需说明。
- 不做北交所专属策略/参数、不做板块统计接口、不在同步页暴露"包含/排除北交所"开关——
  拉全 + 选时过滤已覆盖需求。
- 不做板块归属的时间演化建模（`market` 为当前快照；个股板块属性在存续期内稳定，
  退市符号按 historical 记录处理即可）。
- **选股回测入口不加板块控件**（后端 boards/codes 透传已补齐，API 层面可用；前端以
  策略+日期为核心，板块需求由选股工作台承接）。

## §8 已知限制（如实声明）

1. **策略有效性未验证**：北交所可被选入后，14 个战法的阈值（如 B1 的流通市值 ≥50 亿）
   与北交所标的的适配性**未做实证**——纳入的是数据能力，不是策略承诺；需要用户自行
   选取小样本回测观察。
2. **涨跌幅模型的其他近似**不变：新股上市首日无涨跌幅限制等既有近似不因本 spec 改变。
3. **流动性差异**（北交所成交额小、冲击成本高）不在回测成本模型中建模，属既有近似。
4. suspend_d 对北交所停牌清单的覆盖未验证（仅影响 doubtful 对账在含 BJ 日的行为；
   该场景仅在 ≥2019 极端日触发，概率极低；首触发时人工核对，见 doubtful v2 D4）。
5. **already_booked 护栏**：全量重建的 doubtful 豁免依赖
   `already_booked=store.done_days()`（实测账本 2842 天）。`done_days` 为空的重建
   （首次建库 / `cli.py RESET_PATHS` 含 market/、app.db / `ledger_suspect` 后重建）
   无此豁免——首次建库场景建议跑完 2015 段后人工核对一次。
6. **R22 的定性变更**：doubtful v2 的 `effective.codes` 分子过滤在本 spec 实施后
   **不再是口径修复项**（BJ 已纳入 effective，口径撕裂消失），降级为纯防御
   （防按日帧含清单外码）——doubtful v2 spec 需追加该定性变更注记。
7. **转板股双代码口径**（复审 I6/C1）：3 只转板股（832317→688287 已退、833874→301192、
   833994→301321）在同一系统内有两段独立代码历史（.BJ 精选层段 + 新板块段）——
   选股/回测会将其视为两个独立标的各自进出候选；K 线/报告展示为两只票。
   如未来需合并视图（拼接 .BJ 旧段 + 新板块段），属独立需求。
8. **检测灵敏度**：分母 +6.17%（5218→5561）后，0.95 线对应的"允许缺失绝对只数"
   从 261 升至 278（+17）——细截断的检测灵敏度等比例下降约 6%。
