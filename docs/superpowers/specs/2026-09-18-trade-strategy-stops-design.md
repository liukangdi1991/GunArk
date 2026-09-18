# 回测交易策略扩展：3%止损 / 买入日最低价止损 / 绿砖止损

日期：2026-09-18。状态：已实现（TDD）。

## 需求语义（用户 2026-09-18 口述并逐条确认）

三种新交易策略，入场与既有三种统一：**信号日当日收盘价成交（含滑点），当日收盘涨停拒绝**；
T+1：买入当日不判止损，从次日开始。

| value | 名称 | 止损位 | 触发 | 无触发时 |
|---|---|---|---|---|
| `stop_3pct` | 3%止损 | 实际成交价 × (1 − 3%) | 次日起某日收盘 < 止损位 → 当日收盘卖出 | 持满 5 个交易日卖出（D+5 收盘，不含买入日） |
| `buy_day_low_stop` | 买入日最低价止损 | 买入日当日最低价 | 次日起收盘 < 最低价 → 当日收盘卖出 | 同上，持满 5 日 |
| `green_brick_stop` | 绿砖止损 | 无价格止损 | 次日起砖形图指标变**绿砖**（mt_color == green）→ 当日收盘卖出 | 无限持有，直到绿砖；回测窗口结束仍持有 → 期末未平仓 |

共同口径：
- 止损只看**收盘价**，盘中触及不算；触发日以当日收盘价成交（含滑点费用）
- 橙砖（前日绿 + 当日回升且力度更强）是回升，**不触发**；只认绿砖
- 绿砖用标准 MT 参数 n=4, m=6, t=4（全部砖系选股策略与图表同参，2026-09-18 与用户确认统一）
- 无触发持有上限固定 5 日（策略一/二）；绿砖无限持有（`unlimited_hold`）

## 实现

- `ExecutionConfig` 新字段：`stop_loss_pct: float|None`、`stop_loss_at_buy_day_low: bool`、
  `exit_on_green_brick: bool`、`unlimited_hold: bool`
- `Position` 新字段：`stop_price`（入场时按成交价×(1−pct) 固化）、`stop_base_low`（买入日 bar 的 low）；
  `target_sell_date: Optional[date]`（绿砖无计划卖出日 → None）
- 引擎：`_process_exits` 增加 `_is_price_stop_hit`（T+1 守卫 + 两个止损位比较）与
  `_is_green_brick`（按 code 一次算全历史砖色缓存，`compute_mt_brick` n4/m6/t4）；
  `_group_signals_by_date` 在 unlimited_hold 下不再因 fixed-hold 越界丢弃信号、
  target 置 None；`target_sell_date` 相关比较/顺延天数全部判空
- `_collect_dates` 日历重建加双检锁（等锁期间已被重建则命中，不重复全库扫描）
- presenter `_TRADE_STRATEGY_EXECUTION` 增三条映射；`_build_config` 透传新字段
- 前端 `TRADE_STRATEGY_OPTIONS` 增三项（名称不带后缀：3%止损 / 买入日最低价止损 / 绿砖止损）

## 测试

- 契约：三条新映射断言（含 fixed_hold=5 / unlimited 语义）
- 引擎（零成本零滑点下精确断言）：3% 提前触发 / 未触发走满 5 日；
  买入日最低价触发 / 盘中破位收盘未破不触发；绿砖首个绿砖日精确卖出 /
  无绿砖持仓到期未平仓 / 窗口末端信号不因 fixed-hold 越界被丢弃
- E2E：真实选股产物 + 绿砖止损回测跑通，报告参数快照显示生效配置
