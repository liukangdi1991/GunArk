# TrendRadar 选股性能优化设计

> 版本：v9（评审 13 + 复审 10 + 三审 4 + 四审 3 + 五审 1 + 六审 2 + 七审 1 + 八审 1 全部吸收，2026-08-21）

## 目标

将全市场选股从「单日 45 分钟 / 58 日区间 43 小时」优化到「单日秒级 / 58 日区间分钟级」，**行为等价**（同一输入产出完全相同的选中结果），不改行情数据格式、不改回测、不改前端契约。

## 现状与实测瓶颈

当前执行模型：按交易日循环，每个策略每个交易日对**全量 market_data**（5211 只有数据的股票 × 397 日 ≈ 206 万行；股票列表总数为 5549，含无 bars 的新股/停牌股）重算指标 + 逐候选全表过滤。

**实测 profile（bbi_kdj_b1，全市场单日）**：

| 步骤 | 耗时 | 占比 |
|---|---|---|
| 指标一次计算（全量 206 万行） | 1.8s | 3.5% |
| 按日切片 + 当日过滤 | 0.6s | 1.2% |
| **精筛：逐候选全表 filter**（896 候选 × 206 万行） | **48.8s** | **96%** |
| group_by 一次替代长度检查 | 0.09s | — |

**实测规模曲线（经验拟合 ~O(n^1.7–1.8)，理论上限 O(n²)）**：

| 股票数 | 单策略单日耗时 |
|---|---|
| 1000 | 2.3s |
| 2000 | 8.2s |
| 3000 | 15.2s |
| 5549（全市场） | 45.7s |

瓶颈唯一且明确：**精筛对每个候选执行 `df.filter(code == x)` 全表扫描取历史序列**（896 次 × 206 万行 ≈ 18 亿次比较）。指标每日重算是次要浪费（指标是历史数据的函数，58 天循环里每天结果相同）。

## 已确认决策

- **磁盘布局不变**：`storage/market/bars/{code}.parquet` 每股票一个文件（增量同步/回测友好），选股时读一次拼大表。
- **预热不持久化**：指标列是选股 job 内存临时计算（全量一次 1.8s），不落盘；指标可由 bars 随时重算，持久化需引入缓存失效复杂度（同 calendar.parquet 教训），YAGNI。
- **计算范围收缩**：加载范围 = 选股区间 + 指标窗口前置（现有 `extended_start` 逻辑保留）；短选股区间不加载全量历史。
- **行为等价以「优化前先补确定性断言」为回归基准**：现有 selector 测试只断言 `isinstance`/`strategy_id`，不构成行为等价基准——改造前必须为每个 selector 补充固定输入 → 精确 `selected_codes` 的断言（前置步骤）。
- **行为等价的前提声明**：指标函数（compute_kdj/bbi/ma/zx_lines）在扁平拼接列上做滚动窗口，**不按 code 分组**——股际边界处 rolling 会跨入相邻股票，这是**现状既有行为**（无论排序与否都存在，属既有近似语义）。优化保持同一计算方式（同排序顺序下结果逐值相同）。codes 排序归一化使显式 codes 与默认路径行为一致——**无论 stock_meta 原始序如何（主路径读 parquet 依赖 API 顺序，不保证排序；仅 fallback sorted-glob 路径有序），`sorted(codes)` + `sort(["code","date"])` 后均归一化为确定序**——未排序 codes 的边缘场景由「现状依赖输入顺序」归一化为「排序后确定行为」，属合理归一化，在确定性断言基准内以「排序后」为准。
- **核心逻辑改动按 TDD**。
- **内存声明（实测）**：全量 market_data（5211 只有数据股票 × 397 日，10 列）实测 **128MB**（`estimated_size('mb')`）；单个 warmup（全量 + 指标列 ~90MB + 归档）约 **250MB 级**（partition 若共享底层则更低）；runner 同时持有 10 个策略 warmup ≈ **2.5GB 级**；`market_sync` 已互斥，**selection 未互斥**，两个并发 selection 峰值 ≈ **5GB 级**。部署建议 ≥8GB；低内存环境应避免并发 selection（不引入 selection 互斥，由部署环境保证）。

## 核心设计

### 1. 类型迁移（Protocol / SelectionContext / SelectionResult 完整新定义）

**`trendradar/domain/strategy/protocol.py`**：

```python
@dataclass(frozen=True)
class WarmupResult:
    """warmup 的显式产出：按 code 归档的预计算数据（含指标列）。"""
    grouped: dict[str, pl.DataFrame]   # code -> 该股票完整 DataFrame（[code,date] 有序，含指标列）

class SelectionStrategy(Protocol):
    def warmup(self, market_data: pl.DataFrame) -> WarmupResult:
        """选股开始前调用一次：对全量 market_data 预计算指标列 + 按 code 归档。"""

    def select_day(self, context: SelectionContext, warmup: WarmupResult) -> SelectionResult:
        """每个交易日调用：从 warmup.grouped 按 code 取值判断，不再重算指标。"""

@dataclass(frozen=True)
class SelectionContext:
    trade_date: date
    candidate_codes: list[str] | None   # None = 全市场候选；当前 runner 始终传具体列表，保留 None 语义以备扩展
    market_data: pl.DataFrame           # 保留：warmup 前的原始全量（含当日数据）

@dataclass(frozen=True)
class SelectionResult:
    strategy_id: str
    strategy_name: str
    trade_date: date
    selected_codes: list[str]
    elapsed_seconds: float
```

- **删除 `SelectionContext.get_data_dict`**（selectors 目录零使用；其 `df.filter(code==x)` 恰是 96% 瓶颈，职责由 warmup 分组归档替代）。
- 删除旧 `select()`，不留兼容 shim。
- `market_data` 保留（runner 构造、warmup 输入）。

### 2. Runner 改造（`_run_selection`）

```python
# codes 排序归一化（load_bars 前）：默认路径（stock_meta parquet）依赖 API 顺序、
# 不保证排序，显式 codes 可能为任意序；sorted(codes) 统一归一化为确定序，
# 消除对输入顺序的依赖。
codes = sorted(codes)

market_data = market_store.load_bars(codes, extended_start, trading_dates[-1])
# runner 在 warmup 前确保有序（排序责任方）：
market_data = market_data.sort(["code", "date"])

# 阶段一：warmup（每个策略一次，异常隔离；不调用 update_progress）
warmups = {}
selectors = {}
for defn in resolved:
    if ctx.check_cancelled():
        ctx.fail("Cancelled by user")
        return SignalSet(execution_key=ctx.job_id)   # 取消检查：每策略 warmup 完成后
    ctx.log(f"Warmup {defn.strategy_id}")
    selector = defn.selector_class(defn)
    selectors[defn.strategy_id] = selector           # 实例复用：selector 无状态（仅存 definition），
                                                     # warmup 结果经 WarmupResult 显式传递，不写 self
    try:
        warmups[defn.strategy_id] = selector.warmup(market_data)
    except Exception as e:
        ctx.log(f"Error in warmup {defn.strategy_id}: {e}", level="WARN")
        warmups[defn.strategy_id] = None             # 该策略跳过，不影响其余
    ctx.log(f"Warmup done {defn.strategy_id}")

# 阶段二：按日循环 select_day
for date_idx, trade_date in enumerate(trading_dates):
    if ctx.check_cancelled():
        ctx.fail("Cancelled by user")
        return SignalSet(execution_key=ctx.job_id)   # 交易日粒度取消检查
    ctx.update_progress(date_idx + 1, len(trading_dates), str(trade_date))

    # 逐日 context 构造（与现有逻辑等价）
    day_data = market_data.filter(pl.col("date") == trade_date)
    if day_data.is_empty():
        continue
    day_codes = day_data["code"].unique().to_list()
    candidate_codes = [c for c in codes if c in day_codes]
    if not candidate_codes:
        continue
    context = SelectionContext(
        trade_date=trade_date,
        market_data=market_data,
        candidate_codes=candidate_codes,
    )

    for defn in resolved:
        if ctx.check_cancelled():                    # 逐策略粒度取消检查
            ctx.fail("Cancelled by user")
            return SignalSet(execution_key=ctx.job_id)
        if warmups.get(defn.strategy_id) is None:
            continue                                  # warmup 失败，跳过该策略
        try:
            selector = selectors[defn.strategy_id]   # 复用 warmup 阶段实例（无状态）
            result = selector.select_day(context, warmups[defn.strategy_id])
            if result.selected_codes:
                ...  # 收集信号
        except Exception as e:
            ctx.log(f"Error in strategy {defn.strategy_id} on {trade_date}: {e}", level="WARN")
```

取消检查点：**warmup 每策略完成后一次；select_day 保持现有的「交易日 + 逐策略」双层粒度**——与现状 selection_service.py 一致（内层 `for defn` 逐策略 `check_cancelled`）。

异常隔离：**逐策略 try/except + WARN 日志**，单策略 warmup/select_day 异常不影响其余策略与交易日——与现状一致（selection_service.py 现有行为）。

进度：**warmup 阶段不调用 `update_progress`**（仅 `ctx.log` 记录开始/完成，避免前端进度条从 warmup 100% 跳回 select_day 低值）；select_day 阶段保持现有 `交易日 i/N + 日期` 粒度——与现状一致（前端从日志末条 [PROGRESS] 解析百分比）。

### 3. 精筛分组归档（消除 96% 瓶颈）

- `warmup()` 内：指标计算后按 code 归档。**统一用 `partition_by("code")`**（或等价 dict 构建），产出 `dict[str, pl.DataFrame]`（code → 该股完整序列）。
- `select_day()` 精筛：从 `warmup.grouped[code]` 取值（O(1) 哈希定位 + O(序列长度)），**替代 `df.filter(code==x)` 全表扫描**。
- **排序保证**：runner 在 warmup 前 `sort(["code", "date"])`（单次，保证归档内有序）；精筛内去掉重复 `.sort("date")`。
- **只读约束**：`warmup.grouped` 在 select_day 阶段必须只读访问（polars DataFrame 不可变；dict 本身建议 `MappingProxyType` 包装防误改）。

### 4. Selector 改造分类（9 个类，覆盖 10 个注册策略）

`selectors/` 下 **9 个 selector 类**全部改造（覆盖 **10 个注册策略**，其中 `PerfectB1Selector` 被 `perfect_b1_v2` 与 `perfect_b1_volume_stepdown` 共用，改类一次即覆盖两个策略）。分两类：

- **有指标计算的（8 个）**：bbi_kdj_b1、super_b1、bbi_short_long、peak_kdj、ma60_volume_wave、zxdkx_balance、perfect_b1（compute_kdj 算 J 值）、volume_spike_balance（compute_zx_lines 算趋势线）——warmup 计算各自指标列 + 归档；select_day 从归档取值判断。
- **无指标计算的（1 个）**：big_bullish_volume——直接使用原始 OHLCV（大阳线/上影/倍量判断），warmup 仅做分组归档（不额外算指标），select_day 从归档取序列。

### 5. 指标计算共享 / 策略并行（可选增强，默认不做）

- 指标跨策略共享：warmup 结果按指标名缓存复用——当前指标一次 1.8s，收益边际，YAGNI。
- 策略并行：select_day 循环相互独立可 `ThreadPoolExecutor` 并行；若并行，warmup.grouped 只读（MappingProxyType）满足线程安全（polars DataFrame 不可变）。默认串行，实测后决定。

## 现状基线说明（测量条件差异）

「单日 45 分钟」为**真实运行实测**（2026-08-20 单日、10 策略，含 load_bars 读 5211 个 parquet 的 I/O 与内存分配）。profile 表（单策略 45.7s/日）为**数据已热的单策略微基准**（market_data 已在内存、无磁盘 I/O）——两者测量条件不同，真实运行单日 10 策略含 I/O 开销显著高于 10×微基准。优化后的验收以真实运行对比为准（45 分钟 → <30 秒）。

## 预期收益（基于实测外推）

| 场景 | 现状（真实运行） | 优化后 |
|---|---|---|
| 单日全市场（10 策略） | 45 分钟 | **<30 秒**（验收线；10 策略各自 warmup ≈ 10×1.8s + 每日判断 10 策略 × ~0.15s 哈希取值） |
| 58 日区间全市场 | ~43 小时 | **~2 分钟**（warmup 10×1.8s = 18s + 58 日 × 10 策略 × ~0.15s ≈ 87s，合计 ~105s） |

成本假设区分：**当前** select_day 单日单策略 ~49s（含逐候选全表 filter 48.8s + 按日切片/当日过滤 0.6s，profile 微基准）；**优化后** ~0.15s（仅按日切片 + O(1) 哈希取值，96% filter 消除）——**加速比 ~330x**。跨策略指标共享为 YAGNI 不做，预热按每策略独立计（10×1.8s）。若未来需要更快，策略并行或指标共享可再降数倍。

收益来源：指标计算从「每天每策略一次」变为「每次选股一次」；精筛从「896 次全表扫描」变为「1 次归档 + 896 次 O(1) 取值」。

## 测试策略（行为等价回归）

### 前置步骤（改造前，行为等价基准）

- 为 9 个 selector 测试**补充确定性断言**：固定 seed 输入（构造固定小样本 market_data），断言 `selected_codes` 精确列表——先固化优化前行为，作为回归基准。

### 迁移清单

- `tests/domain/test_selectors/` 9 个测试文件：每个测试的 `select(ctx)` 调用改为 `warmup(df)` + `select_day(ctx, warmup_result)`（每文件 ~2 个调用点，共 ~18 处），断言保持（含新增确定性断言）。
- `tests/domain/test_selectors/helpers.py`：数据构造 helper 保持（不含 get_data_dict）。
- `tests/domain/test_selectors/` 9 个测试文件各自的 `_make_context()` 局部函数：**删除 get_data_dict 参数**，适配新 SelectionContext 签名。
- `tests/app/test_selection_cancel.py`：`SlowSelector` 测试替身实现旧 `select()`——改为 `warmup` + `select_day`（sleep 放 select_day 内模拟长计算）。
- `tests/app/test_execution_registration.py` 等 runner 级测试：`_run_selection` 新流程（warmup + select_day）由真实 selector 驱动，现有断言（executions/artifacts/取消）应保持。

### 新增测试

- warmup 只调用一次、select_day 按日调用（mock/计数）。
- 归档结果与旧 filter 一致性（小样本对照：`partition_by` 取值 == 逐候选 filter 取值）。
- **浮点等价**：warmup 后的指标列与旧版每日计算的指标列**逐值相等**（`pl.DataFrame.equals`，小样本至少 1 个 selector）。
- 取消检查点：warmup 阶段取消响应、select_day 阶段取消响应（沿用 cancel 测试模式）。
- 规模对比（可选）：100 只 × 10 日优化前后耗时宽松断言。

## 验收标准

- 全市场单日选股（10 策略）耗时从 45 分钟降至 **< 30 秒**（实测）。
- **确定性断言下选中结果与优化前完全一致**（9 个 selector 前置基准 + 优化后回归）。
- 取消/进度/日志语义不变（warmup 每策略后 + select_day 原粒度）。
- 58 日区间全市场分钟级完成（实测）。
- 全量测试通过，无回归。

## 非目标

- 不改行情数据格式、存储布局、增量同步。
- 不改回测引擎、前端、API 契约。
- 不做指标持久化缓存（预热 1.8s 成本可接受）。
- 不改策略算法（参数、阈值、信号逻辑完全不变）。
- **batch 参数**：`submit_batch_selection` 的 `batch_size`/`batch_interval_days` 当前不影响执行逻辑（本就未使用），后续如需分批在 runner 层实现，本次不涉及。
