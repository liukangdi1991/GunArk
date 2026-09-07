# 超短线 回测交易策略 设计文档

**日期**: 2026-08-24
**状态**: 已确认（用户 3 点决策锁定）

---

## 1. 背景与需求

新增回测交易策略「超短线」：**信号日 T 收盘价买入 → 持股 1 日 → T+1 收盘价卖出**。与现有引擎模型（信号 T → T+1 开盘买入 → T+N+1 收盘卖出）在**入场时点和入场价格**上不同，需要引擎支持。

## 2. 用户决策（已确认）

| 场景 | 处理 |
|---|---|
| 买入日**开盘涨停** | **放弃**该买入（沿用现有 `reject_if_limit_up_on_buy` 对 open 的检查） |
| 卖出日**收盘跌停** | **顺延**到下一交易日收盘价卖出（`postpone_if_limit_down_on_sell`；2026-09-03 起改为**顺延到底**、无次数上限，见 `2026-09-03-limit-down-postpone-to-end-design.md`） |
| 现有两个止损选项 | **不接线、不生效保持现状**——只给超短线开通道 |

## 3. 设计

### 3.1 配置（`ExecutionConfig` 新增两个字段）

```python
@dataclass(frozen=True)
class ExecutionConfig:
    ...
    entry_on_signal_day: bool = False   # 信号日当日入场（默认 T+1 入场）
    entry_at_close: bool = False        # 入场价用收盘价（默认开盘价）
```

`_build_config` 透传两个字段（`request["execution"]`）。

### 3.2 引擎时序（`engine.py`）

`_group_signals_by_date`：

```python
buy_idx = sig_idx + (0 if self.config.execution.entry_on_signal_day else 1)
sell_idx = buy_idx + hold   # hold = fixed_hold_n_days
```

- 常规：buy_idx=sig_idx+1, sell_idx=buy_idx+hold（行为不变）
- 超短线：buy_idx=sig_idx（T 日入场）, sell_idx=sig_idx+1（T+1 卖出，hold=1）

`_process_entries`：入场价

```python
open_price = float(row["close"] if self.config.execution.entry_at_close else row["open"])
```

涨停检查**保持不变**（对 `row["open"]` 检查——用户决策：开盘涨停即放弃）。

### 3.3 trade_strategy 通道（presenter）

`trade_strategy → execution` 映射（仅超短线；其余不映射 = 现状）：

```python
_TRADE_STRATEGY_EXECUTION = {
    "ultra_short": {
        "entry_on_signal_day": True,
        "entry_at_close": True,
        "fixed_hold_n_days": 1,
    },
}
```

`backtest_from_selection` 与 `selection_backtest` 两条通道都注入 `execution`。

### 3.4 前端

- `TRADE_STRATEGY_OPTIONS` 增加：`{ label: "超短线（信号日收盘买入，次日收盘卖出）", value: "ultra_short" }`
- `describeTradeRule`：当 `entry_on_signal_day` 时显示"信号日收盘买入，T+1 收盘卖出"（替代通用"T+N+1 卖出"文案）

### 3.5 前置校验

`validate_backtest_prerequisites` 的 `needed = fixed_hold_n_days + 1`：超短线 hold=1 → needed=2（T 买 + T+1 卖）✅ 公式天然兼容；日志文案"T+1 买入"在超短线场景修正为"信号日买入"（cosmetic）。

## 4. 影响面

- 文件：`domain/backtest/config.py`、`engine.py`、`app/services/backtest_service.py`（_build_config 透传）、`interfaces/api/presenters.py`（映射）、`frontend/src/utils/tradeStrategy.ts`、`frontend/src/components/StrategySnapshots.tsx`
- 测试：
  - 引擎：超短线时序（T 收盘买入、T+1 收盘卖出）、开盘涨停放弃、收盘跌停顺延、常规模式无回归
  - presenter：trade_strategy=ultra_short → execution 注入（两条通道）
  - 前端：build 通过
- 不接线现有止损（保持现状）

## 5. 非目标

- 不修复现有 trade_strategy 止损选项（用户明确"不用生效"）
- 不改动其他交易规则（止损/资金模式）
