# 回测报告「参数快照」改为展示实际生效配置

> 状态：已实施，后端实跑验证；前端仅 `tsc -b` + `vite build` 通过，浏览器渲染未实测。
> 日期：2026-09-05

## 场景

回测报告页有一块「交易规则」（参数快照），本来是用来回答"这份报告的数是怎么跑出来的"：
默认持有几天、涨停买不买、跌停卖不卖得掉、佣金印花税滑点各是多少。

## 问题

它当时展示的是**前端当时请求了什么**（`jobs.request_json` 里的 `execution` 覆盖字典），
不是**引擎实际按什么跑的**。差别在被省略的字段上：

- 用户没动过"默认持有天数"，请求里就没有 `fixed_hold_n_days` 这个键；
- 但引擎用的是 `ExecutionConfig` 的默认值 5 天，实际确实按 5 天平的仓。

结果报告里这一格只能落回含糊的一句「卖出日按当前交易规则执行」——
一份 5 天持仓的回测说不清自己持了几天。成本、手数干脆完全没展示。

同一段里还有个纯 bug：`StrategySnapshots.tsx` 判断长期牛熊线强制卖出的条件是
`params["连续两日收盘低于长期多空线强制卖出"]`——一个中文键名，`trade_rule` 里从来
没有过这个键。所以只要请求里没带上这个覆盖，那条规则永远不显示，跟用户实际选没选无关。

## 决定

参数快照回答"这次实际按什么规则跑的"。展示**生效配置**，不展示请求覆盖。

## 实现

### 后端：`trendradar/interfaces/api/presenters.py`

- 新增 `_effective_trade_rule(request)`：把存下来的 `jobs.request_json` 喂回
  `backtest_service._build_config()`——也就是引擎真正用的那套默认值由它兜底——再
  `dataclasses.asdict` 导出 `execution`，并补上 `capital.lot_size`、整个 `costs`、
  以及 `params.trade_strategy`。
- 关键点是**复用 `_build_config`** 而不是在 presenter 里重写一份默认值，
  否则引擎改默认值时快照会静默失真。
- `_backtest_config()` 因此去掉 `execution` / `trade_strategy` 两个原始键，改返回
  `trade_rule`；`backtest_result_payload()` 里 `"trade_rule": config["trade_rule"]`。

### 前端：`frontend/src/components/StrategySnapshots.tsx`

- `reject_if_limit_up_on_buy`、`postpone_if_limit_down_on_sell` 从"无条件输出"改成按
  生效值开关输出（关掉了就不该写在报告里）。
- 长期牛熊线那条换成真实键名 `force_sell_on_two_day_close_below_long_term_bull_bear_line`。
- 新增 `describeCosts()`：渲染佣金 / 单笔最低佣金 / 卖出印花税 / 过户费 / 买卖滑点 / 一手股数。
  过户费按 bp 显示——`0.00001` 走百分比两位小数会渲染成 `0.00%`，看着像不收费。

### 同日补录：`position_limits`

上文导出后，同日复查持仓槽位口径（backlog ⑤，决定「不动代码，只让它可见」）时发现
同样的盲区：`target_positions` / `max_positions` / `max_single_position_pct` /
`max_daily_new_positions` 也不在快照里，"每天限开 5 只"的报告和不限的报告长得一模一样。
于是 `_effective_trade_rule` 再多导出 `position_limits`（仍是 `asdict(config.portfolio)`
取这四键，不重写默认值），前端新增 `describePositionLimits()` 渲染在 `describeCosts()`
之前，四项全空时输出「不限持仓数量、开仓节奏与单票占比（选出多少买多少）。」

## 验证

实跑产物 `20260904_064120_backtest_85b9f9e9`，`GET /api/backtest-results` 与
`.../report` 都返回：

```json
{"fixed_hold_n_days":5,"reject_if_limit_up_on_buy":true,"postpone_if_limit_down_on_sell":true,
 "force_sell_on_two_day_close_below_long_term_bull_bear_line":false,
 "close_below_recent_low_stop_window":null,"entry_on_signal_day":false,"entry_at_close":false,
 "lot_size":100,
 "costs":{"commission_rate":0.0003,"commission_min":5.0,"stamp_duty_rate_sell":0.0001,
          "transfer_fee_rate":1e-05,"slippage_buy_bp":2.0,"slippage_sell_bp":2.0},
 "trade_strategy":null}
```

请求里没带 `fixed_hold_n_days`，报告现在能说"默认持仓 5 个交易日，T+6 按收盘价卖出"。

- `tests/app/test_backtest_report_fields.py`：+2（`test_report_trade_rule_shows_effective_defaults`、
  `test_report_trade_rule_reflects_overrides_and_nested_shape`）——后者覆盖
  `selection_backtest` 的嵌套 `backtest.execution` 形状，确认没被覆盖的字段仍取默认值。
- 全量 `.venv/bin/pytest -q`：504 passed。
- `npx tsc -b`、`npx vite build`：通过。
- 新测试**故意不钉** `stamp_duty_rate_sell` 的取值（只断言字段存在），见下方待决项。

## 遗留

`_build_config` 把 `stamp_duty_rate_sell` 默认成 `0.0001`（0.01%），而
`domain/backtest/config.py` 里 `CostConfig` 的默认是 `0.0005`（0.05%，2023-08 起的现行
A 股卖出印花税）。上面实跑 payload 的 `0.0001` 说明服务提交路径永远走前者、
dataclass 默认值不可达——即所有真实回测的卖出印花税少收 4bp。取值口径另议。
