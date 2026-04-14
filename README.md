# Stock Selector

基于多种技术指标的 A 股选股系统。

## 项目结构

```
stock_selector/
├── configs.json              # 选股策略配置（共用）
├── stocklist.csv             # 股票清单（共用）
├── data/                     # CSV 行情数据
├── db/                       # Parquet 行情数据
├── results/                  # 选股结果
├── backtest_results/         # 回测结果
│
├── pandas_version/           # Pandas 版（原版）
│   ├── Selector.py           #   选股器类定义
│   ├── select_stock.py       #   选股入口
│   ├── run.py                #   一键选股（含数据拉取）
│   ├── fetch_kline.py        #   数据拉取（Tushare）
│   ├── backtest.py           #   回测
│   └── requirements.txt
│
├── polars_version/           # Polars 版（高性能版，~68x 加速）
│   ├── Selector.py           #   选股器类定义（numba 加速）
│   ├── select_stock.py       #   选股入口（预筛+精筛+并行）
│   ├── run.py                #   一键选股（含数据拉取）
│   ├── fetch_kline.py        #   数据拉取（Tushare → Parquet）
│   ├── polars_data_manager.py#   Parquet 数据管理
│   └── requirements.txt
│
└── tools/                    # 工具脚本
    ├── benchmark.py          #   性能对比
    ├── compare_data.py       #   数据一致性对比
    └── compare_pandas_polars.py  # 选股结果对比
```

## 两个版本

| | Pandas 版 | Polars 版 |
|---|---|---|
| 数据格式 | CSV (`data/`) | Parquet (`db/`) |
| 选股器 | `Selector.py` | `Selector.py` (numba JIT) |
| 选股逻辑 | 逐只遍历 | 预筛+精筛+并行 |
| 耗时（参考） | ~130s | ~8s |
| 依赖 | pandas, tushare, scipy | polars, numba, scipy |

两个版本的**选股结果完全一致**。

## 安装依赖

```bash
# Pandas 版
pip install -r pandas_version/requirements.txt

# Polars 版
pip install -r polars_version/requirements.txt

# 全部安装
pip install -r requirements.txt
```

## 使用方法

### Pandas 版 - 一键选股

```bash
python pandas_version/run.py

# 指定日期
python pandas_version/run.py --date 2024-01-15

# 跳过数据拉取
python pandas_version/run.py --skip-fetch
```

### Pandas 版 - 单独选股

```bash
python pandas_version/select_stock.py --date 2024-01-15
```

### Polars 版 - 一键选股

```bash
# 自动更新数据，选最新交易日
python polars_version/run.py

# 跳过数据更新，直接选最新交易日
python polars_version/run.py --skip-fetch

# 计算 2025年3月 所有交易日（方便回测）
python polars_version/run.py 202503

# 计算 2025年1月~3月 所有交易日
python polars_version/run.py 202501-202503

# 只运行指定策略
python polars_version/run.py --strategies 少妇战法 暴力K战法
```

### Polars 版 - 单独拉取数据

```bash
python polars_version/fetch_kline.py
```

### Polars 版 - 单独选股

```bash
python polars_version/select_stock.py --date 2024-01-15
```

## 选股器说明

| 战法 | 类名 | 核心指标 |
|---|---|---|
| 少妇战法 | BBIKDJSelector | BBI 上升 + KDJ J 低位 + DIF>0 + MA60 + 知行线 |
| SuperB1战法 | SuperB1Selector | 历史匹配少妇 + 盘整 + 当日下跌 + J 极低 |
| 填坑战法 | PeakKDJSelector | 峰值检测 + KDJ J 低位 + 知行线 |
| 补票战法 | BBIShortLongSelector | BBI 上升 + RSV 条件 + DIF>0 |
| 上穿60放量战法 | MA60CrossVolumeWaveSelector | MA60 上穿 + 放量 + 斜率正 + J 低位 |
| 暴力K战法 | BigBullishVolumeSelector | 大阳线 + 放量 + 知行线 |

## 工具

```bash
# 性能对比
python tools/benchmark.py

# 选股结果对比
python tools/compare_pandas_polars.py
```
