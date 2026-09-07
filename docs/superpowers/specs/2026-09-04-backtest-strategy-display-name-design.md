# 回测报告策略展示：id 当机器键，中文名由 API 层补

日期：2026-09-04　状态：已实施并实跑验证（见文末「验证」）　决策：②

## 为什么要改

同一个策略，选股结果页显示「填坑战法」，点进回测报告页变成 `peak_kdj`。回测侧 4 张表
（策略汇总 / 成交明细 / 跳过明细 / 期末未平仓）的「策略」列、交易收益走势卡的蓝色标签、
回测列表页的「策略：xxx 等 9 个」，全是英文 id。

根因：回测产物只存 id（`TradeRecord.strategy = sig.strategy_id`），`_backtest_summary`
直接把它当展示值用；而选股侧 `_selection_summary` 用的是 `strategy_name`。两侧口径不同。

## 决定

用户口径：「后端主要存 id，也有一个英文的 strategy name，然后前端有一个映射的中文名」。
落地时映射表不硬编码在前端，而是**由后端从策略注册表现查**（注册表是中文名的唯一事实源，
新增/改名策略不需要动前端）。

- `strategy` 字段**保持 id 不变**——它是前端的 rowKey、筛选键、走势卡分组键，
  换成中文会在策略重名/改名时撞键，也和磁盘上 `result.json` 的内容对不上。
- API 层给每行补 `strategy_name`：注册表查得到就是中文名，**查不到留 `None`**
  （策略已被删除的历史报告），前端 `strategy_name || strategy` 回落到 id，不报错。
- `run.strategies`（纯展示，前端只做 `compactStrategyNames` 拼接）改成中文名列表。

## 影响面

- `presenters.py`：新增 `_strategy_display_name(id)` + `_with_strategy_names(rows)`；
  `backtest_result_payload` 里 summary 行、include_report 分支的 trades/skips/
  open_positions 三张明细、`run.strategies` 共 4 处接上
- `types/backtest.ts`：`BacktestSummary` / `BacktestTrade` / `BacktestSkip` /
  `BacktestOpenPosition` 各加 `strategy_name?: string | null`
- `BacktestReportTables.tsx`：加 `strategyLabel(record)` 助手，4 个策略列加
  `render`（`dataIndex` 仍是 `strategy`，排序/筛选不受影响）
- `TradeReturnTrendCards.tsx`：分组键仍是 `trade.strategy`，标签改用
  `trades.find(t => t.strategy_name)?.strategy_name || strategy`
- **前端筛选逻辑零改动**：`BacktestReportPage` 的 `selectedStrategies` /
  `selectedSet.has(item.strategy)` / 4 个 rowKey 全部继续用 id

## 验证

实跑（:8902，真实运行时 `/tmp/e2e-real`，回测 `20260904_064120_backtest_85b9f9e9`）：

```
strategies = ['B1战法', '补票战法', '暴力K战法', '上穿60放量战法', '填坑战法', ...]
summary[0] = {'strategy': 'peak_kdj',      'strategy_name': '填坑战法',   'trade_count': 3625}
trades[0]  = {'strategy': 'peak_kdj',      'strategy_name': '填坑战法',   'code': '000002', 'name': '万科A'}
skips[0]   = {'strategy': 'bbi_short_long','strategy_name': '补票战法',   'reason': '涨停无法买入'}
open[0]    = {'strategy': 'zxdkx_balance', 'strategy_name': '多空平衡选股策略', 'code': '002731'}
```

`/api/backtest-results` 列表侧同样返回中文名。

测试 **502 passed**（+3：`test_report_summary_and_trades_carry_strategy_name`、
`test_report_open_positions_carry_strategy_name`、
`test_report_strategy_name_falls_back_to_id_when_unregistered` 覆盖注册表查不到时
`strategy_name is None` 且 `strategies` 回落 id）。`tsc -b` 无错。
`test_backtest_results_list_shape` 的 `run.strategies` 断言按新契约改成中文名。

浏览器渲染未实测（本环境无前端 dev server 与浏览器工具），验证面止于 payload 与类型检查。
