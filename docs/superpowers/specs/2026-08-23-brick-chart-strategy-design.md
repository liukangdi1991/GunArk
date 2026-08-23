# 砖型图 选股策略 设计文档

**日期**: 2026-08-23
**状态**: 已确认（公式解码经用户确认；DKK 按用户提供公式实现）

---

## 1. 背景与需求

用户提供通达信公式，转换为新选股策略「砖型图」：

```text
N:=4;  M:=6;  T:=4;  M1:=14;  M2:=28;  M3:=57;  M4:=114;

VAR1A:=(HHV(HIGH,N)-CLOSE)/(HHV(HIGH,N)-LLV(LOW,N))*100-90;
VAR2A:=SMA(VAR1A,N,1)+100;
VAR3A:=(CLOSE-LLV(LOW,N))/(HHV(HIGH,N)-LLV(LOW,N))*100;
VAR4A:=SMA(VAR3A,M,1);
VAR5A:=SMA(VAR4A,M,1)+100;
VAR6A:=VAR5A-VAR2A;
MT:=IF(VAR6A>T, VAR6A-T, 0);

RED:=MT > REF(MT,1);
GREEN:=MT < REF(MT,1);
RED_H:=MT - REF(MT,1);
GREEN_H:=REF(MT,1) - MT;

C1:=RED AND REF(GREEN,1) AND RED_H >= REF(GREEN_H,1);
C2:=REF(GREEN,1) AND REF(GREEN,2) AND REF(GREEN,3);

ZXK:=EMA(EMA(C,10),10);        // 计算但未参与选股（死变量，跳过）
DKK:=(MA(C,M1)+MA(C,M2)+MA(C,M3)+MA(C,M4))/4;
C3:=CLOSE >= DKK;

XG:C1 AND C2 AND C3;
```

## 2. 公式解码（已确认）

- **SMA(X,N,M)** 为通达信中国式递归平滑：`Y_t = (M*X_t + (N-M)*Y_{t-1})/N`。本公式三处均为 **M=1** → 等价于 `ewm_mean(alpha=1/N, adjust=False)`：
  - `VAR2A = ewm(VAR1A, alpha=1/4) + 100`
  - `VAR4A = ewm(VAR3A, alpha=1/6)`
  - `VAR5A = ewm(VAR4A, alpha=1/6) + 100`
- `MT = max(VAR5A - VAR2A - T, 0)`（T=4）
- `ZXK` 死变量，不实现（注释说明）
- **DKK 按用户确认的四线平均**：`(MA14+MA28+MA57+MA114)/4`

## 3. 设计

### 3.1 计算分层（可测性拆分）

**`compute_mt(high, low, close, n=4, m=6, t=4) -> pl.Series`**（纯函数，单测目标）：
- 每代码内计算（`partition_by("code")` → 组内计算 → concat），避免跨股票递归污染（新策略不受旧基线约束，SMA 递归比滚动窗口更不能跨界）
- 组内：
  ```
  hh = high.rolling_max(n);  ll = low.rolling_min(n)
  var1 = (hh - close)/(hh - ll)*100 - 90
  var2 = var1.ewm_mean(alpha=1/n, adjust=False) + 100
  var3 = (close - ll)/(hh - ll)*100
  var4 = var3.ewm_mean(alpha=1/m, adjust=False)
  var5 = var4.ewm_mean(alpha=1/m, adjust=False) + 100
  mt = (var5 - var2 - t).clip(lower_bound=0)
  ```

**selector `BrickChartSelector`**（`strategy_id="brick_chart"`, `name="砖型图"`）：
- warmup：`compute_mt` + `dkk = (ma14+ma28+ma57+ma114)/4`（`rolling_mean` 四线平均），均按 code 分组计算后 concat，再 `partition_by` 归档
- select_day（judge 最后一行，需 `row(-5)`，`len(hist) >= 115` 守卫——MA114 需要 114+1 行）：
  ```
  mt_t, mt_1, mt_2, mt_3, mt_4 = 最近 5 个 MT 值
  red   = mt_t > mt_1
  green1 = mt_1 < mt_2;  green2 = mt_2 < mt_3;  green3 = mt_3 < mt_4
  red_h  = mt_t - mt_1;  green_h1 = mt_1 - mt_2
  C1 = red AND green1 AND red_h >= green_h1
  C2 = green1 AND green2 AND green3
  C3 = close_t >= dkk_t
  选中 ⇔ C1 AND C2 AND C3
  ```
- `REQUIRES_MARKET_CAP = False`（无需市值）
- default_params：`{"n": 4, "m": 6, "t": 4, "m1": 14, "m2": 28, "m3": 57, "m4": 114}`

### 3.2 边界与错误处理

| 场景 | 行为 |
|---|---|
| `hh - ll == 0`（4 日内高低点重合） | 除零 → NaN → 条件不满足，排除 |
| `len(hist) < 115` | 跳过（MA114 数据不足） |
| MT 前若干行为 NaN（ewm 从首值起算） | 最后一行判断不受影响（取最后 5 个值） |
| ewm 跨股票污染 | 已按 code 分组消除 |

## 4. 影响面

- 文件：`selectors/brick_chart.py`（新建）、`selectors/__init__.py`（注册）、`tests/domain/test_selectors/test_brick_chart.py`（新建）、`tests/interfaces/test_api_contract.py`（策略数 10→11）
- 测试：
  - `compute_mt` 单测：小样本手算验证 ewm 链（构造简单序列，断言 MT 序列值）
  - select_day 决策测试：构造 hist（含手工 MT 序列 + close/dkk 列）直测 C1/C2/C3——选中用例（red + 前 3 日 green + red_h≥green_h1 + close≥dkk）与多个排除用例（非 red / green 中断 / red_h<green_h1 / close<dkk）
  - API 契约：策略数 10→11
- 前端零改动

## 5. 非目标

- 不修 `compute_zx_lines`（review #1 独立事项，另行处理；本策略自带正确公式不受影响）
- 不实现死变量 ZXK
