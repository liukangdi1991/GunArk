# 跌停顺延改为「顺延到底」+ 期末未平仓单独展示

日期：2026-09-03　状态：已实施（引擎 / 服务 / API / 前端）

## 为什么要改

卖出路径原来是「跌停顺延，最多 10 个交易日；数到上限就按当日收盘价强制成交」。
`postpone_if_limit_down_on_sell` 存在的前提是"跌停封死卖不掉"，而强制平仓这一步
用的正是同一个跌停收盘价——等于自己否掉自己的前提。三个缺陷同源：

1. **强制平仓价不可成交**。连续跌停的第 11 天，那笔卖出以当日（仍封死）收盘价入账，
   现金回笼、仓位消失、盈亏锁定，而现实中这份钱还没回来。收益被高估的方向是确定的：
   越是继续跌的票，被强制平仓后"少亏"得越多。
2. **停牌期标记价回落到成本**。`_get_latest_close` 只取当日那一根 K 线，取不到就用
   `entry_price`。停牌第一天，浮盈在净值曲线上凭空消失，画出一条根本没发生过的回撤；
   停牌前的涨幅越大，这条假回撤越深。
3. **顺延计数器不重置**。`planned_sell_attempts` 全代码只有 `+=1` 和一处 `>` 判定，
   从不归零。额度是 10 天，但计数不区分"哪一次卖出"：止损线（长期多空线连续跌破 /
   近期低点）比计划卖出日更早触发、又被跌停挡住 3 天，等计划卖出日到、真正需要顺延时
   只剩 7 天额度。同一只票同一段行情，能顺延几天取决于中间有没有触发过别的卖出条件。

## 决定

- **顺延到底**：跌停封死就是卖不掉，不假装成交。没有次数上限，没有强制平仓。
  `ExecutionConfig.max_sell_postpone_days` 与 `Position.planned_sell_attempts` 一并删除
  （既然顺延到底，计数器就没有意义——它唯一的用途就是决定什么时候开始骗人）。
- **停牌期标记价 = 最后一根真实收盘价**。`Position.last_close` / `last_close_date` 随
  行情更新，取不到当日行情就沿用上一根。净值不再出现"停牌抹平浮盈"的假回撤。
- **为什么在顺延，必须看得见**。仓位上记 `blocked_since` / `blocked_reason`
  （`跌停封死` / `停牌无行情`），卖出意图解除时归零，下次从头算。
- **到最后一个可用交易日还在顺延的，单独给一个位置显示**：新增
  `OpenPositionRecord` → 报告 `open_positions` 数组 + `metrics.open_position_count`，
  前端回测报告页新增「期末未平仓（卖不掉）」区块。它不是"跳过记录"——
  跳过记录是买不进/卖不出的**一次性**事件，这里是钱还压在里面的**存量**仓位。
- **口径澄清**：`metrics.final_cash` 是期末**组合市值**（现金 + 未平仓仓位标记价），
  与净值曲线末点相等。此前服务层把它改写成"最后一天的现金"，有未平仓仓位时
  恰好少掉那一块，而前端列名叫"最终现金（组合）"。前端列名改为「期末市值（组合）」。

## 副作用（可接受，但要写明）

只要还有仓位卖不掉，主循环就会一路跑到行情最后一天（`not state.positions` 才 break），
净值曲线的期数变长，`annual_return_pct` 的分母随之变大——这不再是被强制平仓"提前收工"
撑出来的高年化，而是这笔钱真实被占用的时长。

## 影响面

- 引擎：`trendradar/domain/backtest/engine.py`（`_process_exits` / `_mark` / `_open_record`）、
  `models.py`（`Position` 新增 3 个字段、删除 1 个，新增 `OpenPositionRecord`）、
  `config.py`（删除 `max_sell_postpone_days`）
- 服务：`backtest_service.py` 序列化 `open_positions` + `open_position_count`、
  落 `open_positions.parquet`、`metrics.json` 与报告同一份 dict、任务日志逐条提示
- API：`presenters.py` 报告 payload 新增 `open_positions`（补 name/industry）、
  策略汇总的 `open_positions` 由硬编码 0 改为真实计数、策略清单纳入只有未平仓的策略
- 前端：`types/backtest.ts` + 报告页新区块 + 汇总表「未平仓」列

读旧 `result.json`（没有 `open_positions` 字段）不会报错，返回空数组；
历史回测报告的数字仍是旧口径，**需要重跑才会反映新口径**。
