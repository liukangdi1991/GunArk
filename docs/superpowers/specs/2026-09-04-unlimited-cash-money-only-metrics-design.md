# unlimited_cash 模式改为「只算逐笔盈亏」，不再产净值曲线

日期：2026-09-04　状态：已实施，实跑验证见文末「验证」

## 为什么要改

`capital.mode` 有两个取值。`realistic` 真的扣现金、真的按权益定仓位，所以它有组合含义，
净值曲线是它的主产物。`unlimited_cash`（**默认**）恰恰相反：`engine.py:265-266` 的预算
固定为 `fixed_cash_per_trade`，**不读权益**；`:287-288` 也不校验现金够不够。它是
「每个信号投一份名义额」的**采样器**——目的不是模拟一个账户，而是统计这套战法在样本上
赚不赚钱、赚多少。

采样器却一直在输出账户指标，因为 `compute_summary` 拿的是同一份净值曲线。全市场实跑
（`20260903_105335_backtest_de887fb4`，13 个交易日、5,031 笔、期末未平仓 1 笔）：

| 项 | 实测 |
| --- | --- |
| 首日曲线点 | cash **−233,383,984**，持仓 4,816 只，equity 457,733 |
| 末日曲线点 | equity **−5,504,764** |
| 13 天中权益为负的天数 | **12** |
| `total_return_pct` | **−1302.62%** |
| `max_drawdown_pct` | **−2190.52%** |
| `annual_return_pct` | **−100.00**（被夹断） |
| `sharpe` | **−4.65** |
| 同一份数据里真实的钱 | Σ买入面额 244,754,005，Σ盈亏 **−6,479,143** ⇒ **−2.65%** |

−1302.62% 不是夸张，是**假数**，四道全塌：

1. `metrics.py:32-34` 的分母取曲线首点，而首点已经是**当日买入后**的市值（457,733，
   不是 100 万本金）。分子分母都跟"本金"无关，比值可以任意大。
2. 净值穿越 0 ⇒ `nav / cum_max - 1` 可以小于 −100%（−2190.52%），而回撤的定义域是 [−100%, 0]。
3. `annual_return` 的底数 `1 + total_return` 为负 ⇒ 分数次幂出复数，第三轮已夹断成 −100%。
4. `sharpe` 用 `nav / nav.shift(1) - 1`，首个观测就是 −2190%，整条序列的均值与方差被这一个
   点主导。（另一次实跑同样亏钱却报 **+269.04**：权益为负时 `nav` 为负，日收益符号整体反转。）

而这一模式**真正该看**的那个数（−2.65%）此前不在 `metrics.json` 里，只在作业日志
（`total_return=-56.79%`，另一个口径）和前端「逐笔盈亏」卡片里各算一遍——三处三个数。

## 决定

**`unlimited_cash` 不计算净值曲线。** 上限无限、现金可以为负，就不存在"这个账户现在值多少钱"
这个问题；顺着取消的还有日终那次全持仓标记（实测占回测总耗时的大头，见第八轮）。

- 引擎：`mode == "unlimited_cash"` 时跳过日终 `mark_prices` / `current_equity` /
  `equity_curve.append`，`result.equity_curve == []`，`equity.parquet` 不落盘。
  未平仓仓位的标记价改为**循环结束后统一标一次**——标记价是 `_mark` 在推进
  `pos.last_close`，不标会让「期末未平仓」区块的标记价停在成本、浮动盈亏恒为 0
  （第九轮实测的 ST 顺延票就靠这个字段）。
- 指标：组合级净值指标一律 `None`（不是 0.0——0% 会被读成"不赚不亏"）：
  `total_return_pct` / `annual_return_pct` / `max_drawdown_pct` / `sharpe` /
  `initial_cash` / `final_cash`。
- 新增逐笔金额口径（`metrics.py:compute_money_summary`）：
  `realized_profit_sum`（Σ 已实现净盈亏，含全部费用）、`invested_notional_sum`
  （Σ 买入面额 `buy_price × shares`）、`pnl_return_pct`（前者 / 后者，投入为 0 时 `None`）、
  `unrealized_pnl`（Σ 未平仓浮动盈亏）。分母用买入面额，与 `presenters._backtest_summary`
  的按策略收益率、前端 `TradeReturnTrendCards` 的累计收益率**同一口径**（三份实跑逐策略
  对照 Δ=0.00pp），不再制造第四个数。
- `realistic` 模式一切不变。

## 影响面

- 引擎/指标：`engine.py`（日终标记按模式跳过、末尾统一标记未平仓、`_compute_metrics` 分派）、
  `metrics.py`（新增 `money_summary`，`compute_summary` 复用它并新增 `NAV_METRIC_KEYS`）
- 服务：`backtest_service.py` 完成日志改为按模式选口径（旧行用 `:.2f` 格式化，
  拿到 `None` 会 `TypeError`），`equity_curve` 自然为空 ⇒ 不写 `equity.parquet`
- API：`presenters.py` —— `_backtest_summary` 停止把组合级净值数**逐行复制**到每个策略
  （那是同一个假数被贴 N 遍）；报告 `start_date`/`end_date` 改由成交/未平仓日期推导
  （原从曲线首末点取，曲线没了就空白）
- 前端：`types/backtest.ts` 四个净值字段可为 null；汇总表按 `capital_mode` 分支——
  `unlimited_cash` 显示「累计盈亏 / 累计投入 / 盈亏收益率 / 未平仓浮动盈亏」，隐藏
  最大回撤（组合）/Sharpe（组合）/期末市值（组合）三列

## 历史报告

落盘的 `metrics.json` 是旧口径写死的，**不重跑不会变**。但报告页面读的不是它：
`_backtest_summary` 用 `trades`/`open_positions` 现算总收益，前端又按 `capital_mode`
隐藏净值列，所以**旧的 unlimited 报告不重跑也会显示新口径**（实测见下）。唯一留下的
是完成日志里的历史行（`total_return=-1302.62%`），那是日志、不参与渲染。

## 验证

同一份信号集（`20260903_105046_selection_5bc6dd3c`，14 天全市场、109 个信号日）重跑：

| 项 | 改前 `20260903_105335` | 改后 `20260904_032431` |
| --- | --- | --- |
| `equity.parquet` | 有（13 点） | 不再生成 |
| `result.json` 曲线 | 13 点 | 0 点 |
| 六项净值指标 | −1302.62 / −2190.52 / −100 / −4.65 / 1,000,000 / −5,504,764 | 全部 `null` |
| Σ盈亏 / Σ投入 | — | −6,479,142.88 / 244,754,005.01 ⇒ **−2.6472%** |
| 未平仓浮动盈亏 | — | −25,621.47（1 笔，停牌顺延 8 日） |
| trades/skips/open_positions | md5 616468b022a2 / 90588e83d994 / 69891a931aad | **逐字节相同** |
| 报告窗口 | 2026-08-18 → 2026-09-03 | 相同（改由成交日推导） |
| 完成日志 | `total_return=-1302.62%` | `pnl=-6,479,143 / invested=244,754,005 (-2.65%)` |

`trades` 等三张表逐字节相同，证明交易路径没被动过；老报告 `20260903_105335` 用新代码
读出来，`total_return_pct` 也变成 −2.8155%（策略级）、净值三列为 `null`。

测试 477 passed（+9 新用例），`tsc -b` 无错。前端无净值图表组件（`recharts` 只被
`TradeReturnTrendCards` 使用，它本来就是逐笔金额口径），所以除汇总表列外无需改动。
