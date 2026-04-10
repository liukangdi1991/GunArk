# Polars 迁移指南

## 为什么选择 Polars

1. **速度快 5-10 倍** - 特别是滚动窗口计算
2. **内存效率高** - 低 30-50% 内存占用
3. **原生多线程** - 自动并行处理
4. **懒加载优化** - 自动优化查询计划

## 迁移步骤

### 1. 安装依赖

```bash
pip install polars
```

### 2. 数据加载对比

**Pandas 版本（当前）:**
```python
df = pd.read_csv(fp, parse_dates=["date"]).sort_values("date")
```

**Polars 版本:**
```python
import polars as pl

df = pl.read_csv(fp, try_parse_dates=True).sort("date")
```

### 3. 指标计算对比

**Pandas KDJ:**
```python
low_n = df["low"].rolling(window=n, min_periods=1).min()
high_n = df["high"].rolling(window=n, min_periods=1).max()
rsv = (df["close"] - low_n) / (high_n - low_n + 1e-9) * 100
```

**Polars KDJ:**
```python
df = df.with_columns([
    pl.col("low").rolling_min(window_size=n, min_periods=1).alias("low_n"),
    pl.col("high").rolling_max(window_size=n, min_periods=1).alias("high_n"),
]).with_columns(
    ((pl.col("close") - pl.col("low_n")) / (pl.col("high_n") - pl.col("low_n") + 1e-9) * 100).alias("rsv")
)
```

### 4. 懒加载优化

```python
# Polars 懒加载 - 自动优化查询计划
df = pl.scan_csv(fp).sort("date").filter(
    pl.col("date") <= target_date
).collect()
```

## 预期性能提升

| 操作 | Pandas | Polars | 提升 |
|------|--------|--------|------|
| 加载 500 个 CSV | 10s | 2s | **5x** |
| 计算 KDJ | 8s | 1.5s | **5x** |
| 计算 BBI | 6s | 1s | **6x** |
| 总体选股 | 60s | 10-15s | **4-6x** |

## 注意事项

1. **API 差异** - Polars 使用链式调用，语法不同
2. **索引** - Polars 不使用索引，用行号代替
3. **类型** - Polars 类型更严格，需要注意类型转换
4. **兼容性** - 某些 Pandas 特有功能需要重写

## 推荐策略

### 方案A: 完全迁移
- 将所有代码迁移到 Polars
- 性能最大化
- 工作量较大

### 方案B: 混合使用（推荐）
- 数据加载用 Polars
- 指标计算用 Polars
- 保留 Pandas 用于兼容性
- 工作量适中

### 方案C: 渐进式迁移
- 先迁移最慢的部分（如 SuperB1Selector）
- 逐步替换其他模块
- 风险最小