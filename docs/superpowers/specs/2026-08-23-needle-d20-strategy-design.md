# 单针下20 选股策略 + 按需流通市值数据 设计文档

**日期**: 2026-08-23
**状态**: 已实现（2026-08-23，5 任务 TDD 完成，278 passed；线上验证选股成功）

---

## 1. 背景与需求

用户提供通达信公式，转换为新选股策略「单针下20」：

```text
N1:=3;  N2:=21;
短期:=100*(C-LLV(L,N1))/(HHV(C,N1)-LLV(L,N1));
长期:=100*(C-LLV(L,N2))/(HHV(C,N2)-LLV(L,N2));
流通市值:=FINANCE(40)/100000000;
XG: 短期<=20 AND 长期>80 AND 流通市值>=50;
```

语义：21 日区间内仍处高位（长期随机指标 > 80）但 3 日随机指标已下探 ≤ 20（急跌回踩、中期趋势未破），且流通市值 ≥ 50 亿元。

项目当前**没有市值数据**（行情仅 OHLCV/amount/adj_factor，stock_meta 无市值字段）。

## 2. 已验证事实（2026-08-23 实测）

- Tushare `daily_basic` 接口字段：**`circ_mv` = 流通市值（万元）**（官方文档），`total_mv` = 总市值（万元）。
- 权限：daily_basic 需 **≥2000 积分**；用本项目 token 实拉 `trade_date=20260821` 成功返回 5543 行全市场 → **权限可用**。
- 语义抽查：600519 circ_mv≈1.59 万亿元、000001 circ_mv≈2214 亿元，与公开流通市值一致；`circ_mv/total_mv ∈ (0.037, 1.0]`，流通 ≤ 总市值，语义正确。
- 换算：50 亿元 = **500,000 万元** → 条件 `circ_mv >= 500000`。

## 3. 设计：市值按需拉取 + 进程级内存缓存

**不做数据管线改动**（不加 parquet 列、不改同步、不重同步存量数据）。

```
selection_service 提交选股
  → 解析策略集合
  → 任一策略声明 REQUIRES_MARKET_CAP → 需要市值
  → 对选股区间内每个交易日:
        daily_basic_circ_mv(pro, trade_date)     # infrastructure, 1 次调用=全市场
        → 进程级缓存 {trade_date: {code: circ_mv_万元}}   # 日期不可变，无需 TTL
  → 装配当日 SelectionContext.market_cap
  → selector 在 select_day 读 context.market_cap[code]
```

### 3.1 domain（纯业务，保持纯净）

```python
@dataclass(frozen=True)
class SelectionContext:
    trade_date: date
    candidate_codes: list[str] | None = None
    market_data: pl.DataFrame = field(default_factory=pl.DataFrame)
    market_cap: dict[str, float] | None = None   # 新增：当日全市场 code→流通市值(万元)
```

Selector 能力声明（类属性）：`REQUIRES_MARKET_CAP: bool = False`；需要市值的 selector 置 True。

### 3.2 infrastructure

```python
_MARKET_CAP_CACHE: dict[date, dict[str, float]] = {}

def daily_basic_circ_mv(pro, trade_date: date) -> dict[str, float]:
    """当日全市场 流通市值(万元) → {code: circ_mv}; 进程级缓存按日。"""
    if trade_date in _MARKET_CAP_CACHE:
        return _MARKET_CAP_CACHE[trade_date]
    resp = pro.daily_basic(trade_date=trade_date.strftime("%Y%m%d"),
                           fields="ts_code,circ_mv")
    result = {}
    for row in resp.to_dict(orient="records"):
        code = str(row["ts_code"])[:6]
        mv = row["circ_mv"]
        if mv is not None and mv == mv:  # 非 NaN
            result[code] = float(mv)
    _MARKET_CAP_CACHE[trade_date] = result
    return result
```

（位置：`trendradar/infrastructure/tushare/market_cap.py` 新文件，复用 `get_pro` 约定；调用方传入 pro。）

### 3.3 app（selection_service 装配）

- 解析策略后：`need_mcap = any(sel.REQUIRES_MARKET_CAP for sel in resolved)`
- 需要时：对 `trading_dates` 每个日期调 `daily_basic_circ_mv`，构造 `context = SelectionContext(..., market_cap=mc)`（每个交易日一个 context）
- 失败处理：daily_basic 调用异常 → 记录 warning 并**跳过市值条件**（该日 context.market_cap=None，selector 对应策略该日不选股，不使整个作业失败）

### 3.4 Selector「单针下20」（`single_needle_down_20`）

- `REQUIRES_MARKET_CAP = True`
- warmup（扁平列滚动窗口，house 模式）：
  ```python
  short = 100 * (C - L.rolling_min(3)) / (C.rolling_max(3) - L.rolling_min(3))
  long  = 100 * (C - L.rolling_min(21)) / (C.rolling_max(21) - L.rolling_min(21))
  ```
- select_day：
  ```python
  short <= short_max(20) AND long > long_min(80)
  AND context.market_cap 非空 AND market_cap[code] >= circ_mv_min_yi * 10000 (50亿=500000万)
  ```
- default_params：`{"n1": 3, "n2": 21, "short_max": 20, "long_min": 80, "circ_mv_min_yi": 50}`
- 注册进 `register_all()`：`strategy_id="single_needle_down_20", name="单针下20", description="3日随机指标下探≤20 + 21日区间高位>80 + 流通市值≥50亿"`

## 4. 边界与错误处理

| 场景 | 行为 |
|---|---|
| 某代码当日无 circ_mv（新股/停牌） | 该股排除（市值条件不满足） |
| daily_basic 拉取异常 | warning + 该日 market_cap=None，相关策略该日不选股，作业不失败 |
| 批量选股长区间（如 58 天） | 每天 1 次调用（58 次，限流内）；缓存跨策略/跨作业复用 |
| 无策略声明需要市值 | 零额外调用，行为与现状完全一致 |
| 历史回测 | 不涉及（市值只在选股期判定，signals.json 已固化结果） |

## 5. 影响面

- 文件：`domain/strategy/protocol.py`（context 字段）、`selectors/single_needle_down_20.py`（新建）、`selectors/__init__.py`（注册）、`infrastructure/tushare/market_cap.py`（新建）、`app/services/selection_service.py`（装配）
- 测试：`test_api_contract.py` 策略数 9→10；`SelectionContext` 协议测试（新字段默认 None）；新公式单测；selector 行为测试（mock market_cap）；`daily_basic_circ_mv` 缓存/NaN 处理测试；selection_service 装配测试（声明策略触发拉取、异常降级）
- 前端零改动（策略自动出现）

## 6. 非目标

- 不做市值持久化（parquet 列/同步扩展）——按需拉取已满足需求，未来若需回测期市值可再议
- 不做市值历史缓存落盘（进程重启后重新拉取，代价小）

## 7. 已知 open question

- `daily_basic` 按日调用是否受 6000 行上限约束：实测 5543 行 < 6000 ✅（>5700 行时的降级预案参照现有 daily 处理）
