# 极致砖型图选股 策略 设计文档

**日期**: 2026-08-23
**状态**: 已实现（2026-08-23，TDD 完成，310 passed；线上 23/24 对齐，1 浮点边界案例）

---

## 1. 背景与需求

用户提供通达信公式，转换为新选股策略「极致砖型图选股」（`ultimate_brick_chart`）：

```text
N:=4;  M:=6;  T:=4;  M1:=14;  M2:=28;  M3:=57;  M4:=114;
VAR1A..MT（同砖型图）
C1:=RED AND REF(GREEN,1) AND RED_H>=REF(GREEN_H,1);
C2:=REF(GREEN,1) AND REF(GREEN,2) AND REF(GREEN,3);
ZXK:=EMA(EMA(C,10),10);
DKK:=(MA(C,M1)+MA(C,M2)+MA(C,M3)+MA(C,M4))/4;
C3:=ZXK>DKK;  C4:=CLOSE>=ZXK;
XG:C1 AND C2 AND C3 AND C4;
```

## 2. 语义（已确认）

| 条件 | 语义 | 精确公式（judge row(-1)） |
|---|---|---|
| C1 | 今日红柱，昨日绿柱，红柱高度 ≥ **昨日那根绿柱**高度（`>=` 非 `>`） | `mt_t>mt_1 AND mt_1<mt_2 AND mt_t-mt_1 >= mt_2-mt_1` |
| C2 | **今日之前连续 3 日**（t-1/t-2/t-3）绿柱 | `mt_1<mt_2 AND mt_2<mt_3 AND mt_3<mt_4` |
| C3 | 短期线高于长期四线均值（强势） | `zxk_t > dkk_t` |
| C4 | 收盘站上短期线 | `close_t >= zxk_t` |

传递性：`close ≥ ZXK > DKK` ⟹ `close > DKK`（比砖型图的趋势过滤严格）。

## 3. 设计

### 3.1 指标计算（复用既有基建）

- `MT`：复用 `formulas/mt_oscillator.compute_mt`（按 code 分组）
- `ZXK = ewm(ewm(close, 2/11))`：按 code 分组（ewm 递归禁跨股票）
- `DKK`：复用 `compute_zx_lines`（扁平列，判定行 MA114 窗口在股内）
- 全部判定值 null/NaN 显式守卫；`len(hist) >= 115`（DKK 需 MA114）

### 3.2 select_day

```python
C1: mt_t>mt_1 AND mt_1<mt_2 AND mt_t-mt_1 >= mt_2-mt_1
C2: mt_1<mt_2 AND mt_2<mt_3 AND mt_3<mt_4
C3: zxk_t > dkk_t
C4: close_t >= zxk_t
```

### 3.3 default_params / 注册

`{"n": 4, "m": 6, "t": 4, "m1": 14, "m2": 28, "m3": 57, "m4": 114, "ema1": 10}`

`strategy_id="ultimate_brick_chart"`, `name="极致砖型图选股"`,
`description="MT 绿转红(≥昨绿高) + 前3日绿柱 + 多头排列(close≥ZXK>DKK)"`。策略数 12→13。

## 4. 影响面

- 文件：`selectors/ultimate_brick_chart.py`（新建）、`selectors/__init__.py`（注册）、`tests/domain/test_selectors/test_ultimate_brick_chart.py`（新建）、`tests/interfaces/test_api_contract.py`（12→13）
- 测试：MT/ZXK 参考实现交叉验证 + 决策用例（C1-C4 各条件独立排除 + 全满足选中）+ 边界（NaN/短历史）
- 前端零改动

## 5. 非目标

- 不新增 runner/基建改动（全部复用）
