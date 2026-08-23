# 超跌抄底 选股策略 设计文档

**日期**: 2026-08-23
**状态**: 已实现（2026-08-23，TDD 完成，310 passed；线上 4/4 对齐通达信）

---

## 1. 背景与需求

用户提供通达信公式，转换为新选股策略「超跌抄底」（`oversold_bottom_fishing`）：

```text
N:=4;  M:=6;  T:=4;  M1:=14;  M2:=28;  M3:=57;  M4:=114;

VAR1A:=(HHV(HIGH,N)-CLOSE)/(HHV(HIGH,N)-LLV(LOW,N))*100-90;
VAR2A:=SMA(VAR1A,N,1)+100;
VAR3A:=(CLOSE-LLV(LOW,N))/(HHV(HIGH,N)-LLV(LOW,N))*100;
VAR4A:=SMA(VAR3A,M,1);
VAR5A:=SMA(VAR4A,M,1)+100;
VAR6A:=VAR5A-VAR2A;
MT:=IF(VAR6A>T, VAR6A-T, 0);

RED:=MT>REF(MT,1); GREEN:=MT<REF(MT,1);
RED_H:=MT-REF(MT,1); GREEN_H:=REF(MT,1)-MT;
C1:=RED AND REF(GREEN,1) AND RED_H>=REF(GREEN_H,1);

DD2:=C-2*REF(C,1)+REF(C,2);
C2:=DD2>0 AND DD2=HHV(DD2,5);

ZXK:=EMA(EMA(C,10),10);
DKK:=(MA(C,M1)+MA(C,M2)+MA(C,M3)+MA(C,M4))/4;
C3:=ZXK<DKK;  C4:=CLOSE<ZXK;

DIFF:=EMA(CLOSE,12)-EMA(CLOSE,26);
C5:=EVERY(DIFF<0,5) AND DIFF-REF(DIFF,1)>=0 AND REF(EVERY(DIFF-REF(DIFF,1)<0,4),1);

XG:C1 AND C2 AND C3 AND C4 AND C5;
```

## 2. 公式解码（已确认）

| 部分 | 语义 | 实现 |
|---|---|---|
| MT 振荡器 | 与砖型图完全相同 | **复用 `compute_mt`**（提取至 `formulas/` 共享） |
| C1 | MT 转升 + 昨日绿 + 升幅≥昨跌幅 | `mt_t>mt_1 AND mt_1<mt_2 AND mt_t-mt_1 >= mt_2-mt_1` |
| C2 | 收盘二阶差分 >0 且为近 5 日最高（**含今日**，窗口 t-4..t） | `dd2_t>0 AND dd2_t==max(dd2[t-4..t])` |
| C3 | ZXK < DKK（**ZXK 这次参与选股**） | `zxk_t < dkk_t` |
| C4 | 收盘 < ZXK | `close_t < zxk_t` |
| C5 | 近 5 日 DIF 全负（含今日 t-4..t）+ 今日 DIF 走平/回升 + 今日之前 4 天 DIF 持续下行（t-5..t-1） | 见 §3.1 |

## 3. 设计

### 3.1 指标计算（全部按 code 分组，递归/位移类禁跨股票）

warmup 按 `partition_by("code")` 计算后 concat（runner 保证 `[code,date]` 排序，concat 不改变行序）：

```python
# 每股内:
mt   = compute_mt(g["high"], g["low"], g["close"])          # 复用（提取到 formulas/mt_oscillator.py）
zxk  = g["close"].ewm_mean(alpha=2/11, adjust=False).ewm_mean(alpha=2/11, adjust=False)
diff = g["close"].ewm_mean(alpha=2/13, adjust=False) - g["close"].ewm_mean(alpha=2/27, adjust=False)
dd2  = g["close"] - 2 * g["close"].shift(1) + g["close"].shift(2)
dkk  = compute_zx_lines(market_data)[1]   # 扁平列（复用）；判定行 MA114 窗口在股内
```

### 3.2 select_day（judge row(-1)，`len(hist) >= 115` 守卫）

```python
C1: mt_t>mt_1 AND mt_1<mt_2 AND mt_t-mt_1 >= mt_2-mt_1
C2: dd2_t>0 AND dd2_t == max(dd2[t-4..t])
C3: zxk_t < dkk_t
C4: close_t < zxk_t
C5: all(diff[t-4..t] < 0) AND diff_t >= diff_{t-1}
    AND diff_{t-1}<diff_{t-2} AND diff_{t-2}<diff_{t-3} AND diff_{t-3}<diff_{t-4} AND diff_{t-4}<diff_{t-5}
```

### 3.3 边界与错误处理（纳入此前 review 全部教训）

| 场景 | 行为 |
|---|---|
| **停牌/选股日无行情** | runner 层 `_filter_warmup` 已全局排除（该代码不进 warmup） |
| **0-span（一字板）→ MT/指标 NaN** | 所有判定值显式守卫：`is None` 或 `v != v`（NaN）→ 排除；C1-C5 全部为正向比较（NaN → False → 排除）双保险 |
| **DD2/DIFF 前导 null**（shift/窗口不足） | row(-1) 需 115+ 行，窗口充足；仍做 null 守卫 |
| 历史不足 115 行 | 排除（DKK 需 MA114） |
| `DD2 == rolling_max` 浮点相等 | 含今日的最大值即今日自身 → 相等即"今日为最高"；NaN 参与比较 → False → 排除 |
| ewm/REF 跨股票污染 | 全部按 code 分组消除 |

### 3.4 default_params

```python
{"n": 4, "m": 6, "t": 4, "m1": 14, "m2": 28, "m3": 57, "m4": 114,
 "ema1": 10, "dif_fast": 12, "dif_slow": 26, "every_neg": 5, "every_down": 4, "dd2_window": 5}
```

### 3.5 注册

`strategy_id="oversold_bottom_fishing"`, `name="超跌抄底"`,
`description="MT 转升 + 收盘二阶差分新高 + 双线下方超跌 + MACD DIF 拐头"`。策略数 11→12。

## 4. 影响面

- 文件：`formulas/mt_oscillator.py`（新建，`compute_mt` 从 brick_chart 迁出）、`selectors/brick_chart.py`（改 import）、`selectors/oversold_bottom_fishing.py`（新建）、`selectors/__init__.py`（注册）、`tests/interfaces/test_api_contract.py`（11→12）
- 测试：
  - 参考实现交叉验证（完整公式链：MT/ZXK/DIFF/DD2/C1-C5，对齐 polars null/NaN 语义）
  - 决策用例直测（各条件独立排除 + 全满足选中）
  - 边界：0-span → NaN 排除；短历史排除；DD2 窗口 NaN
  - 复用 `compute_mt` 回归（砖型图测试不动）
- 前端零改动

## 5. 非目标

- 不新增 runner 改动（停牌过滤已全局生效）
- 不修其他挂账项
