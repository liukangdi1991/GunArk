# Stock Selector

基于多种技术指标的股票选股程序，参考 StockTradebyZ 项目实现。

## 项目结构

```
stock_selector/
├── run.py             # 🚀 一键选股程序（推荐使用）
├── Selector.py        # 选股器类定义
├── select_stock.py    # 选股程序入口
├── fetch_kline.py     # 数据拉取程序
├── configs.json       # 选股器配置文件
├── stocklist.csv      # 股票清单
├── requirements.txt   # Python 依赖
├── data/              # 股票行情数据目录（CSV 格式）
├── results/           # 选股结果保存目录
└── README.md          # 项目说明
```

## 安装依赖

```bash
pip install -r requirements.txt
```

## 使用方法

### 🚀 一键选股（推荐）

使用 `run.py` 一键完成数据拉取和选股：

```bash
# 一键选股（自动拉取数据 + 运行选股）
python run.py

# 指定选股日期
python run.py --date 2024-01-15

# 跳过数据拉取，直接选股（已有数据时）
python run.py --skip-fetch

# 指定数据拉取起始日期
python run.py --start 20230101

# 排除特定板块
python run.py --exclude-boards gem star bj
```

**输出示例：**
```
╔══════════════════════════════════════════════════════════════╗
║                    📈 股票选股系统 📈                         ║
║                  Stock Selection System                      ║
╚══════════════════════════════════════════════════════════════╝

┌──────────────────────────────────────────────────────────┐
│  🎯 少妇战法                                              │
├──────────────────────────────────────────────────────────┤
│  📅 交易日: 2024-01-15                                    │
│  📊 符合条件: 3                                           │
├──────────────────────────────────────────────────────────┤
│    1. 000001 平安银行                                      │
│    2. 000002 万科A                                         │
│    3. 600036 招商银行                                      │
└──────────────────────────────────────────────────────────┘
```

选股结果会保存到 `results/` 目录，文件名格式为 `selection_YYYYMMDD.txt`。

### 📥 单独拉取数据

使用 `fetch_kline.py` 从 Tushare 拉取股票行情数据：

```bash
# 使用默认配置拉取数据
python fetch_kline.py

# 指定日期范围
python fetch_kline.py --start 20230101 --end today

# 排除特定板块
python fetch_kline.py --exclude-boards gem star bj

# 指定股票清单和输出目录
python fetch_kline.py --stocklist ./my_stocklist.csv --out ./data
```

**注意**：需要设置环境变量 `TUSHARE_TOKEN`，或在代码中使用默认 token。

### 🔍 单独运行选股

使用 `select_stock.py` 运行选股（需要先有数据）：

```bash
# 使用默认配置
python select_stock.py

# 指定交易日期
python select_stock.py --date 2024-01-15

# 指定数据目录
python select_stock.py --data-dir ./my_data

# 只分析特定股票
python select_stock.py --tickers 000001.SZ,000002.SZ
```

## 选股器说明

### BBIKDJSelector（少妇战法）
- BBI 导数上升趋势
- KDJ J 值低位
- MACD DIF > 0
- 收盘价在 MA60 上方
- 知行线条件

### SuperB1Selector（SuperB1战法）
- 历史匹配 BBIKDJ 条件
- 盘整区间波动率控制
- 当日下跌条件
- J 值极低

### PeakKDJSelector（填坑战法）
- 峰值检测
- KDJ J 值低位
- 知行线条件

### BBIShortLongSelector（补票战法）
- BBI 上升趋势
- 短期/长期 RSV 条件
- MACD DIF > 0

### MA60CrossVolumeWaveSelector（上穿60放量战法）
- MA60 上穿条件
- 成交量放大
- MA60 斜率正
- KDJ J 值低位

### BigBullishVolumeSelector（暴力K战法）
- 大阳线条件
- 成交量放大
- 知行线条件

## 输出结果

选股结果会同时输出到控制台和 `select_results.log` 日志文件。