# 同步自检 doubtful v2 设计：分段阈值 + suspend_d 精确对账

> 版本：v2.3（2026-09-09）。v1 → v2：吸收三视角评审 19 项发现；v2 → v2.1：吸收复审
> N1-N9；v2.1 → v2.2：吸收复审三 P1-P10（§7 用例名对齐、R22 拆 unit）；v2.2 → v2.3：
> 吸收复审五 R1-R4（§7 R19 全量样本描述、R23 补 runner 取消用例）；v2.3 → 本轮终验
> S1-S4（§7 R22 行撤回已删承诺、版本注记对齐）。前置：2cce2037。

## §1 背景与问题

断言①（行数自检）现行实现为全局一刀切阈值：`actual < 0.75 × expected(d) → doubtful`。
两个问题：

1. **日常增量宽松**：增量只拉近期日子，而 2019 年后正常比值地板 ≈0.97——0.75 的线意味着
   缺损 20% 以内的截断（如响应上限砍掉尾部 500 行，比值 0.91）静默通过。
2. **全量重建在旧时代反复报警**：2015-2016 股灾/停牌潮年代真实比值地板 0.476，
   0.75 与 0.85 之间的日子（107 天）需逐日人工裁决——2cce2037 已用 already_booked 豁免
   + 审计明细缓解，但裁决本身无法自动化。

**北交所口径发现（v2 新增）**：旧 spec 断言"北交所物理不可得"已失效——实测
`daily(trade_date=)` 现返回 BJ 行，增量按日路径已落盘 343 个 BJ 文件（各仅含
2026-09-08/09 两天）。2026-09-09 实测：含 BJ ratio = **1.0638**（5550/5217），
剔 BJ ratio = **0.9981**（5207/5217）。分子含 BJ、分母剔 BJ 的口径污染若不修，
0.95 阈值的真实报警线被稀释到 ≈0.89。处置：**检查时分子按 effective.codes 过滤**
（纯过滤条件，不改落盘语义、不改阈值）。

## §2 需求

| 编号 | 需求 |
|---|---|
| R18 | 断言①阈值按被检日期分段：`year<2017 → 0.75`、`2017≤year<2019 → 0.85`、`year≥2019 → 0.95` |
| R19 | 断言①触发的日期经 suspend_d 独立对账：一致（单边容差 max(5, 2%×应成交)）→ 自动入账 + reconciled 审计 |
| R20 | 对账不可用（接口失败/限频重试耗尽/取消）→ 回退现状 doubtful 路径（能力不降级） |
| R21 | reconciled 与 doubtful 均落 `sync_meta` 审计 + 控制台逐日日志；**本轮无对账日时审计键覆写为空**（不留陈旧值） |
| R22 | 行数统计分子与分母同口径：均按 effective.codes 过滤（含 BJ 剔除） |
| R23 | 对账窗口响应取消：取消即中止换名/落账（staging 保留续传，与拉取期取消同契约），终态 `cancelled`；未对账日重跑自愈 |

（R1-R17 归属 market-sync-redesign spec，见 `2026-08-27-market-sync-redesign-design.md`。）

## §3 实测校准数据（2026-09-09）

### 3.1 分年比值分布

| 年份 | 天数 | p5 | p1 | min |
|---|---|---|---|---|
| 2015 | 244 | 0.800 | 0.520 | 0.476 |
| 2016 | 244 | 0.878 | 0.876 | 0.875 |
| 2017 | 244 | 0.907 | 0.905 | 0.904 |
| 2018 | 243 | 0.911 | 0.891 | 0.889 |
| 2019 | 244 | 0.979 | 0.978 | 0.972 |
| 2020-2026 | ~1175 | 0.978-0.984 | 0.977-0.983 | 0.970-0.978 |

注：本表 min 为**含 BJ 分子**的修复前基线口径；各年 min 所在日均无 BJ 行
（BJ 文件仅含 2026-09-08/09），故 R22 口径修复不影响本表数值。

### 3.2 触发量（两种口径，勿混用）

| 阈值口径 | 触发天数 |
|---|---|
| 分段（D1/R18）：≤2016→0.75、2017-18→0.85、≥2019→0.95 | **7 天**（2015-07-07…07-15，比值 0.476~0.745） |
| 统一 0.95（仅作对照，非本设计） | 875 天 |

### 3.3 suspend_d 对账实验

- 2015-07-08（千股停牌日）：应市 2781 − 停牌 1348 = 应成交 1433；盘上 actual 1446；
  差 **+13 = +36（停牌在列却实际成交：临时停牌/盘中复牌/退整期股）− 23（真停牌但清单漏记）**，
  即 suspend_d 存在 ±1% 量级双向固有噪声 → 对账必须用**单边容差**，精确等式不可用。
- 全史验证：2015-2019 全部 **1219 个交易日**逐日 `suspend_d` 对账，
  容差 max(5, 2%×应成交)：**通过 1219 / 失败 0（100% cover）**。
  差值分布 min=−49、p1=−47、p50=−22、p99=−2、max=+334；空响应盲区 0 天。
- 未验证面：2020-2026 段未做全史 suspend_d 对账实测（该段 0.95 线历史零触发，
  对账路径无历史入口）；首触发时按 R19 流程执行并人工核对一次。

### 3.4 北交所口径（R22）

effective 清单剔 BJ（`build_effective_list`），但 `daily(trade_date=)` 现返回 BJ 行且
增量按日路径已落盘（343 个 BJ 文件，仅 09-08/09 两天）。因此**所有行数统计分子必须
按 `effective.codes` 过滤**：增量路径对当日拉取帧过滤、全量路径 `_staging_day_rows`
按 allowed_codes 过滤、对账分母 `suspended ∩ effective.codes`。
落盘语义不变（BJ bar 照常保留），仅检查口径修正。修复后 2026-09-09 剔 BJ ratio =
0.9981（健康）。

## §4 核心设计

### D1 分段阈值（R18，`threshold_for(day)`）

| 区间 | 阈值 | 依据（该段 p1） |
|---|---|---|
| `year < 2017` | 0.75 | 2015 股灾地板 0.476，维持现状线 |
| `2017 ≤ year < 2019` | 0.85 | p1≈0.89-0.91，留 ~4% 缓冲 |
| `year ≥ 2019` | 0.95 | p1≈0.972-0.983，留 ~2% 缓冲 |

**D1 的真实收益**：常态期（≥2019）报警线从 0.75 收紧到 0.95——细截断盲区从
"缺损 ~25% 才可见"缩小到"缺损 5% 即报"（BJ 口径修复后）。全量重建触发量
875 天（统一 0.95 口径）→ **7 天**（分段口径，见 §3.2）。

### D2 suspend_d 精确对账（R19，触发日第二道）

对 ratio 触发的每个日期：

```
suspended_alive = |suspend_d(day) ∩ effective.codes|
expected_traded = expected_alive(d) − suspended_alive
通过（actual ≥ expected_traded − max(5, 2%×expected_traded)）
    → 该日入账 + reconciled 审计
不通过 → doubtful（现状路径）+ 明细落账
```

单边容差的依据：缺口方向（应成交却无 bar）才可能是拉取截断；多出方向是
suspend_d 漏记/盘中复牌的真实 bar，无害（§3.3 实验）。

**容差量纲**：百分比为主——expected_traded > 250 时 2% > abs 下限 5；
abs 下限仅服务极小应市日。**测试样本必须越过 et=250 临界**（否则对账判定被
abs 容差吞掉、正向用例假绿），实施计划用例规格按 600/300 只设计。

**YAGNI 显式声明**：分段阈值下，对账的常态触发面 = 全量重建时的 7 个历史日
（2015-07）+ 未来极端日；2019+ 段常态零触发。为此引入一个新接口 + 节流 + 退避 +
审计字段的取舍依据：这 7 天目前依赖人工拍板，对账把"极端日裁决"彻底自动化，
且是"缺口 >5% 必拦"承诺（D1 收紧后）唯一的机器裁决手段。

### D3 already_booked 豁免保留（2cce2037，行为不变）

### D4 suspend_d 调用纪律（R19/R20）

- 节流：调用间隔 ≥0.35s（≈170/min < 接口上限 200/min；实测 0.11s/次）
- 限频异常退避 62s 重试，最多 3 次
- 重试耗尽/其它异常/取消 → 该日回退 doubtful（R20，保守：无法对账 = 不自动放行）
- **代价（分段口径实测）**：全量重建触发 7 天 × 0.46s ≈ **+3.2 秒**；
  限频退避最坏 7 × 186s ≈ 21.7 分钟（对账窗口内取消即中止，见 R23，不会烧完）

### D5 审计（R21）

- `doubtful_detail`（已有）：最终 doubtful 集的逐日明细
- `reconciled_days`（新增）：本轮对账通过自动入账的日期清单；**每轮无条件覆写**
  （无对账日写 `[]`），不留陈旧值
- 两者均落 `sync_meta` + 控制台逐日日志；**仅落库供事后 SQL 排查，不做接口暴露**
  （status 接口与前端零变更，见 §6）

### D6 取消语义（R23）

对账循环内 `fetch_suspend_list` 传 `cancel_check`；返回 cancelled 即
**ctx.cancel() 并返回（发生在换名/落账之前）**：

- staging / bars / 账本零改动——与拉取期取消完全同契约（staging 保留续传，
  终态 `cancelled`），不存在"部分对账部分入账"的中间态，无假绿窗口
- 未对账日不入账，重跑自愈（对账重新执行）

## §5 不变式（继承 v1）

- INV-4 幂等：doubtful/reconciled 日数据照常写盘，重拉即 upsert
- P1#4：doubtful 标记与账本同事务，回滚不丢标记
- 断言②③④（覆盖/结构/账本子集）不变

## §6 前端影响
无。status/selection/backtest 接口契约零变更；reconciled_days 仅落 sync_meta，
不进任何接口响应。

## §7 测试计划（R → 用例映射）

| 需求 | 用例（TDD 先行） |
|---|---|
| R18 | `test_threshold_for_bands`、`test_doubtful_detail_uses_band_thresholds`（selfcheck） |
| R19（增量） | `test_run_incremental_doubtful_reconciles_via_suspend_list`（600/300 样本，runner） |
| R19（增量反向） | `test_run_incremental_doubtful_when_suspend_list_empty`（600/300 样本，缺口 300 > 容差 12 → doubtful） |
| R19（增量 service） | `test_r6_incremental_suspension_reconciles_and_books`（600 只样本） |
| R19（全量） | `test_full_doubtful_reconciles_via_suspend_list`（600 只样本，触发日 500/600） |
| R20 | `test_run_incremental_doubtful_when_suspend_list_empty`（增量反向）、`test_r6_reverse_insufficient_reconcile_stays_doubtful`（service 反向）、`test_r6c_reconcile_unavailable_falls_back_doubtful`、`test_fetch_suspend_list_rate_limit_retries_then_env` |
| R21 | reconciled/doubtful meta 断言（并入 R19/R20 用例）+ 无对账轮覆写空清单断言 |
| R22 | `test_staging_day_rows_excludes_codes_outside_allowed`（unit）——全量路径 R22 的真实风险面是续传残留的清单外文件，由 unit 测试直接覆盖；端到端用例不再断言 BJ（BJ 经 `build_effective_list` 剔除后不会进入 tasks） |
| R23 | `test_fetch_suspend_list_cancelled_immediately`（fetch 单测）、`test_run_incremental_cancelled_during_reconcile_aborts_batch`（runner：整批中止）、`test_full_cancelled_during_reconcile_keeps_staging`（service：终态 cancelled + staging 保留） |

既有用例回归要点：`test_runner.py` 全部（`_eff` 改真实 codes + 样本 600 后）、
`test_market_sync_service.py` 的原始 r6 / r13 / r17×4 / 两个 0.952 边界用例
（处置见实施计划 Task 4.2 处置表）。

## §8 YAGNI

- reconcile 不做接口暴露、不建独立表（sync_meta 键值够用）
- 阈值表硬编码三段（不加配置项；调参 = 改常量 + 测试）
- 不改 `filter_excluded_boards`/落盘语义（BJ bar 保留在盘，仅检查口径过滤）——
  若未来要让 BJ 进入选股宇宙，属独立需求

## §9 运维提醒

- 全量重建日志会出现"reconciled {date}"（对账通过自动入账）——正常，非告警
- `suspend_d` 限频 200/min：对账循环自带 0.35s 节流；若与其它 suspend_d 消费方
  并发，可能出现 62s×3 退避，最坏 +21.7 分钟（对账窗口可取消，R23）
- 首次全量重建（新库）预期在 2015-07 出现 7 天 doubtful/对账提示，属已知历史停牌潮
