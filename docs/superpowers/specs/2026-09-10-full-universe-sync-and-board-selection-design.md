# 全市场拉取（含北交所）与选股板块过滤设计

> 版本：v1（2026-09-10）。**修订既有 spec**：取代 `2026-08-27-market-sync-redesign-design.md`
> R16 的"`.BJ` 股永不进入拉取清单与 expected"条款（该条款的立论"Tushare daily 物理不提供
> 北交所行情"已被实测证伪，详见 §3.1）。与 `2026-09-09-sync-doubtful-v2-design.md` 的联动
> 见 §4-D6。
> **实施顺序**：本 spec 评审通过后，先落地 doubtful v2，再实施本 spec（依据见 §4-D6 与
> 计划文档的排序论证）。

## §1 背景与问题

### 1.1 北交所数据的三方不一致（实测）

| 层 | 现状 |
|---|---|
| **清单** | `stock_meta` 5899 行含 **348 只北交所**（`market` 列 = "北交所"） |
| **拉取（全量）** | `build_effective_list`（stocklist.py:98）无差别剔除 `.BJ` → 全量重建**从不拉** → 北交所无历史 |
| **拉取（增量）** | 按日 `daily(trade_date=)` 返回北交所行，且默认无板块过滤 → 行被写盘 |
| **盘上结果** | 343 个北交所文件，**每个仅 2026-09-08/09 两天**（增量覆盖到的日子） |
| **消费（选股）** | 宇宙 = `stock_meta()` 全量（含北交所）→ 每轮进候选但 bars 不足 → 选不出，白读 343 个近乎空文件 |

### 1.2 旧假设已证伪

`stocklist.py:98`、`spec.py:17` 两处注释声称"Tushare daily 物理不提供北交所行情"。实测
（§3.1）：按股、按日、`adj_factor`、`daily_basic` **均已覆盖 920x 号段北交所**。

### 1.3 潜伏缺陷

- **涨跌停规则失效**：`domain/backtest/execution.py:_limit_pct` 的 30% 档按老号段
  `("4","8")` 判定；北交所 2021 年开市后启用 **920x** 新号段（当前 348 只中 345 只为 920x）
  → 落进默认 **10%** 档。北交所实际涨跌幅限制 **±30%**。当前因北交所选不出而未暴露；
  一旦纳入选股/回测，涨跌停判定与成交模拟将系统性错误。
- **选股无板块过滤**：宇宙固定为全市场；API 虽支持 `codes` 白名单但前端未暴露；
  前端无任何板块维度的选择入口。

### 1.4 设计方向（用户拍板）

**同步侧拉全（不做板块剔除）、选股侧按板块过滤**：职责分离——“拉取不用纠结剔除，
选股时选创业板/科创板/北交所/主板”。

## §2 需求

| 编号 | 需求 |
|---|---|
| R24 | 全量拉取覆盖北交所：撤销 `build_effective_list` 的 `.BJ` 剔除；已退市北交所代码走 `OK_EMPTY` 语义（探访为空 = 成功，不写文件、不计失败） |
| R25 | 涨跌停规则覆盖 920x 北交所（±30%）：`_limit_pct` 改用 `is_bse_code` 单一实现源，消除号段漂移风险 |
| R26 | 选股板块过滤：请求新增 `boards`（主板/创业板/科创板/北交所）；默认不传 = 全宇宙（现状不变）；与 `codes` 白名单叠加为交集；非法板块值 400 |
| R27 | 同步侧一致性收敛：`exclude_boards` 机制保留（默认关闭）但撤销"北交所永久剔除"硬编码；前端同步页文案修正为事实描述 |
| R28 | 文档与测试联动：修订 market-sync-redesign R16 记注；doubtful v2 §3.4 加联动注记；相关测试（`test_effective_list_excludes_bj_and_future_listed` 等）同步改写 |

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
| `daily/adj_factor(833874.BJ / 832317.BJ / 833994.BJ)` | ⚠️ 0 行——但这 3 只**均已退市**（2022 年，`delist_date` 非空），无当前影响 |

### 3.2 北交所代码族构成

| 前缀 | 只数 | 说明 |
|---|---|---|
| 920 | 345 | 2021 年开市后的新号段（含原精选层平移） |
| 833/832 | 3 | 全部已退市（2022） |

### 3.3 `stock_meta.market` 列分布（板块过滤的数据基础）

| market | 行数 |
|---|---|
| 主板 | 3484 |
| 创业板 | 1447 |
| 科创板 | 619 |
| 北交所 | 348 |
| null | 1（`T600018`，2006 年退市的历史测试符号） |

### 3.4 代码前缀判板逻辑清查（全仓）

| 位置 | 用途 | 处置 |
|---|---|---|
| `stocklist.py:98` `.endswith(".BJ")` | effective 剔除 | **删除**（R24） |
| `spec.py:13` `BSE_PREFIXES=("92","4","8")` + `is_bse_code` | 裸码→ts_code 映射等 | **保留**（单一实现源，已含 92x） |
| `fetch.py:_to_ts_code` 走 `is_bse_code` | code→ts_code | 无需改（920x → `.BJ` 映射正确） |
| `execution.py:21` `("4","8")` → 0.30 | 涨跌停 | **改用 `is_bse_code`**（R25） |
| `execution.py:28` `("688","689")` → 200 股 | 最小买入单位 | 无需改（北交所 100 股 = 默认档） |
| `fetch.py:EXCLUDE_BOARD_PREFIXES` `{"gem","star"}` | 可选的拉取排除 | 保留（R27；默认关闭） |
| `service.py:54,513` `BSE_UNAVAILABLE_REASON` + `is_bse_code` 三元 | 显式补齐的 BJ 拒绝理由 | **删除**（BJ 可拉后成死分支；`is_bse_code` 导入同步清理） |
| `stocklist.py:69,85` docstring | "L∪D − 北交所" 口径描述 | 更新（去北交所剔除字样） |
| `spec.py:17` `is_bse_code` docstring | "Tushare daily 物理不提供其行情" | 更新（过期假设） |
| `fetch.py:32` 注释 | "北交所改由 .BJ 后缀识别" | 更新（全市场拉取后无剔除语义） |

## §4 核心设计

### D1 撤销 effective 的北交所剔除（R24）

`build_effective_list` 删除 `if ts_code.endswith(".BJ"): continue`（连同过期注释）。
生效面：

- **全量重建**：tasks 含 348 只北交所（比现状 +6.6% 只数）；`_to_ts_code` 已正确映射
  `920xxx → .BJ`；已退市 3 只（833x/832x）拉取为空 → `fetch_one` 的
  `if not frames: return StockOutcome(code, True, OK_EMPTY)` 视为**成功**（不写文件、
  不计失败、不进 `sync_skipped`）。
- **断言①分母**：`expected_trading_count` 随 effective 自然纳入北交所（上市日期起算）。
  已退市 3 只（833x/832x，2022 退市）也会计入其 2020-2022 存续期的 expected，但拉取
  返回空 → 该区间每日 expected 虚高 3 只（≈0.07%，4 千只量级），由分段阈值容差吸收；
  每轮全量对其产生 2 次空返回调用（OK_EMPTY、不写文件、不计失败）。
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

**请求契约**（`ExecutionRequest` 增加字段，`presenters` 透传列表同步）：

```python
boards: list[str] | None = None   # 取值 ∈ {"主板","创业板","科创板","北交所"}；None/空 = 不滤
```

**语义**（与仓储/DB 原生对齐，值即 `stock_meta.market` 列字面量）：

```
无 boards 且无 codes → 全宇宙（现状不变，含 null market 的历史符号）
仅有 boards          → 宇宙 = { code | market(code) ∈ boards }
仅有 codes           → 白名单（现状不变）
boards + codes 同时  → 交集：白名单中 market ∈ boards 的子集
```

- **校验**：`validate_selection_request` 增加 boards 取值校验，非法值 → `ValueError` → 400
  （与既有 group/strategy id 校验同风格）；boards 为空列表按"不传"处理。
- **null market 边界**：`T600018`（2006 退市）在任何板块过滤下都不命中（`market ∉ boards`）；
  该符号在任何现代选股区间均无 bar，无实际影响，但语义在本文档定死。
- **单一改动点**：单次与批量选股共用 `_run_selection`，宇宙派生逻辑只改一处。

### D4 前端（R26/R27）

1. **选股工作台**（`SelectionWorkspacePage`）：新增"板块"多选（主板/创业板/科创板/北交所，
   默认全选四板块），提交时随请求发送 `boards`；结果页已有"所属板块"列，无需改。
2. **同步页**（`MarketDataPage`）：`exclude_boards` 选项保留；占位文案
   "排除板块（默认不排除；北交所永久剔除）" → "排除板块（默认不排除；北交所随全市场拉取）"。

### D5 数据回填（运维步骤）

- 本 spec 上线后执行**一次全量重建**（`force` 路径）补齐北交所历史：
  348 只 × （daily+adj_factor）≈ +700 次调用；北交所历史短（200~650 行/只），
  基线全量 ≈41 分钟 → 预估 **+2~4 分钟**。
- 现有 343 个两天窗的北交所文件**无需清理**——重建即覆盖（INV-4 幂等），且增量路径
  本就在持续追加。重建前北交所数据"只有两天"属已知过渡态。
- **选股侧影响**：宇宙 +348 只（bars 各约 200-650 行）——加载与 warmup 量级 +5-7%，
  可忽略；若用户用 `boards` 反向收窄（如仅主板），实际负载还会下降。

### D6 与既有 spec/计划的联动（R28）

| 文档 | 动作 |
|---|---|
| market-sync-redesign（2026-08-27） | R16 的".BJ 永不进入拉取清单与 expected"被本 spec 取代——在该 spec 的修订记录或本 spec 内留交叉引用（不改旧文正文，保持其评审溯源） |
| doubtful v2（2026-09-09） | §1/§3.4 的"BJ 口径污染"叙事在本 spec 实施后由"纳入统一"解决；**R22 的 `effective.codes` 分子过滤保留**为通用防线（防按日帧含清单外码）——实施本 spec 时在 doubtful v2 spec 追加一行注记 |
| 本 spec 与 doubtful v2 的实施顺序 | **先 v2 后本 spec**：v2 已七轮评审收敛、机制宇宙无关（R22 过滤在含/不含 BJ 两种宇宙下均正确）；本 spec 需自己的评审周期，且其数据回填依赖独立的运维窗口（全量重建） |
| 测试联动 | 见 §5 R28 行（stocklist 断言反转、backfill 用例语义反转、`test_limit_up_price_bse` 扩展、5 处过期字面量清理） |
| 前端文案 | 见 D4-2 |

## §5 测试计划（R → 用例映射）

| 需求 | 用例 |
|---|---|
| R24 | `test_effective_list_includes_bse_codes`（920x 在 effective；已退市 833x 经钳制后区间为空 → 不入清单或 OK_EMPTY 路径）；既有 BJ 排除用例改写 |
| R25 | 扩展既有 `test_backtest_execution.py::test_limit_up_price_bse`（430001 老号段保留）+ 新增 920x 断言（`limit_up_price(10.0, code="920001") == 13.0`）；创业板/科创板 20% 与默认 10% 既有断言保持绿 |
| R26 | `test_selection_boards_filter_universe`（boards=北交所 → 宇宙仅 348）；`test_selection_boards_plus_codes_intersection`；`test_selection_invalid_board_400`；`test_selection_default_universe_unchanged`（不传 boards 行为不变） |
| R27 | 前端文案快照不适用（无前端测试体系）——人工走查；后端 `exclude_boards` 既有用例保持绿 |
| R28 | ① `test_stocklist.py::test_effective_list_excludes_bj_and_future_listed` 改写（920099 进 codes；`clamped_range("920099")` 非 None）；② `test_explicit_backfill_rejects_bse_code` 语义反转并更名 `test_explicit_backfill_accepts_bse_code`（BJ 在册 → 可补齐；"不在册"仍拒绝）；③ 过期字面量 5 处清理（stocklist×3、fetch:32、spec:17、service:54 删除）；④ 全量回归 |

## §6 前端影响

- 选股工作台：新增板块多选（唯一新交互）；提交 payload 增字段。
- 同步页：仅文案修正。
- 其余页面（K 线、回测、行情数据）：零变更。北交所 K 线在全量重建后自然可看（当前仅 2 根）。

## §7 YAGNI

- **不改 `exclude_boards` 机制**（保留但不默认启用；删除它属 API 契约变更，无功能收益）。
- 不做北交所专属策略/参数、不做板块统计接口、不在同步页暴露"包含/排除北交所"开关——
  拉全 + 选时过滤已覆盖需求。
- 不做板块归属的时间演化建模（`market` 为当前快照；个股板块属性在存续期内稳定，
  退市符号按 historical 记录处理即可）。

## §8 已知限制（如实声明）

1. **策略有效性未验证**：北交所可被选入后，14 个战法的阈值（如 B1 的流通市值 ≥50 亿）
   与北交所标的的适配性**未做实证**——纳入的是数据能力，不是策略承诺；需要用户自行
   选取小样本回测观察。
2. **涨跌幅模型的其他近似**不变：新股上市首日无涨跌幅限制等既有近似不因本 spec 改变。
3. **流动性差异**（北交所成交额小、冲击成本高）不在回测成本模型中建模，属既有近似。
4. suspend_d 对北交所停牌清单的覆盖未验证（仅影响 doubtful 对账在含 BJ 日的行为；
   该场景仅在 ≥2019 极端日触发，概率极低；首触发时人工核对，见 doubtful v2 D4）。
