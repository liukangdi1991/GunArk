# 入场去重键改为 (code, strategy)，默认允许平仓后再入场

日期：2026-09-04　状态：已实施并实跑验证（见文末「验证」）

## 为什么要改

`_process_entries` 用 `signal_by_code = {s["code"]: s …}` 按**纯 code** 折叠当日候选。
两个策略在同一天选中同一只股票时，只有 dict 迭代顺序里最后一个能活下来，其余被
**静默丢弃**——不进 `trades`、不进 `skips`、日志里也没有。

实跑信号集 `20260903_105046_selection_5bc6dd3c`（9 个策略、109 个信号日、61,046 个
(date, code) 对）里，**16,987 对（27.8%）被 >1 个策略同时选中**。旧键下这些全部塌缩
成一份持仓，用户完全看不到"如果每个策略独立建仓会怎样"。

## 决定

- 持仓键从 `code` 改为 `(code, strategy)`（`PositionKey = tuple[str, str]`）。
  一个策略一只股票**持仓期间**只买一次；不同策略互不干扰，可同日同票各自建仓。
- 被持仓去重吞掉的选入**不记 skips**（用户明确："重了就不买了"，不需要审计痕迹）。
- `allow_reentry_same_stock` 默认值 `False → True`：用户口径"卖了再选出来还得买"。
  该配置仍保留，设回 `False` 可恢复"同一 (code,strategy) 整轮回测只入场一次"。

## 影响面

- `portfolio.py`：`positions: dict[PositionKey, Position]`、`closed_keys: set[PositionKey]`、
  `open_position/close_position/is_holding` 多传 strategy、`filter_reentry_codes →
  filter_reentry_keys`、`current_equity` 的 `mark_prices` 改按 PositionKey 查
  （避免同 code 多策略持仓时标记价互相覆盖，停牌兜底 entry_price 不同的那份会用错价）
- `engine.py`：`_process_entries` 用 (code,strategy) 建 signal_by_key/candidates/marks memo；
  `_process_exits` 迭代 tuple 键、close_position 传 pos.strategy；日终净值标记循环同步
- `config.py` + `backtest_service.py`：`allow_reentry_same_stock` 默认 True
- `__init__.py`：导出 `PositionKey`、`filter_reentry_keys`，移除 `filter_reentry_codes`
- 前端/API payload **无变化**：TradeRecord/SkipRecord/OpenPositionRecord 本来就带
  strategy 和 code 字段，序列化形状不变
- **配置语义微调**：`max_positions` / `max_daily_new_positions` 的计数单位从
  "不同 code 数"变为"(code, strategy) 对数"。同一只票被两个策略持有现在占两个槽位，
  与"每个策略独立建仓"的口径一致

## 验证

同一份信号集（`20260903_105046_selection_5bc6dd3c`，9 策略 / 109 信号日 / 14 天全市场）
重跑 `20260904_064120_backtest_85b9f9e9`，对比改前 `20260904_032431`：

| 项 | 改前（code 键） | 改后（(code,strategy) 键） |
| --- | --- | --- |
| trade_count | 5,031 | **16,382**（+11,351） |
| 有成交的策略数 | 6 / 9 | **9 / 9** |
| 完全被吞掉的策略 | ma60_volume_wave、bbi_kdj_b1、super_b1（0 笔） | 无 |
| Σ盈亏 | −6,479,143 | **+8,401,707** |
| Σ投入 | 244,754,005 | 797,355,821 |
| 盈亏收益率 | −2.65% | **+1.05%** |
| 胜率 | 31.09% | 55.96% |
| 期末未平仓 | 1 | 9 |
| 净值指标 | 全 null | 全 null（决定 A 不受影响） |
| 耗时 | 245s | 390s（持仓数 ×3.26，线性增长） |

旧键下 `signal_by_code = {s["code"]: s …}` 是 dict 推导式，**迭代顺序里最后一个策略
吃掉该 code 的槽位**，其余静默丢弃。bbi_short_long 恰好排在后面，独吞 3,679 笔；
ma60_volume_wave / bbi_kdj_b1 / super_b1 的所有选入都与靠后策略碰撞，**整轮零成交**。
这不是"少记了几笔"，是三个策略在报告里根本不存在。

完成日志：`16382 trades, win_rate=55.96%, pnl=8,401,707 / invested=797,355,821 (1.05%), 未平仓 9 unrealized=-50,184`

测试 487 passed（+10：7 portfolio 键语义 + 3 engine 去重/再入场），`tsc -b` 无错
（前端 payload 形状未变，无需改动）。
