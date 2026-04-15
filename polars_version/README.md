# Polars 版本说明（详细版）

本目录是项目的高性能实现版本，目标是在**不改变策略逻辑结果**的前提下，利用 `Polars + Numba` 提升选股速度。

---

## 1. 目录结构

```text
polars_version/
├── Selector.py              # 各策略指标与筛选逻辑（含 numba 加速内核）
├── select_stock.py          # 单日选股入口
├── run.py                   # 一键流程：数据拉取 + 选股（支持月份批量）
├── fetch_kline.py           # Tushare 拉取日线并写入 Parquet
├── polars_data_manager.py   # CSV/Parquet 管理工具
└── README.md                # 本文档
```

---

## 2. 设计原则

- 与旧版 Pandas 保持一致的策略行为和信号输出结构。
- 优先向量化预筛，再做精筛，减少 Python 层循环。
- IO 统一走 Parquet（默认 `db/`），降低读写成本。
- 配置驱动策略启停和参数，不硬编码在主流程里。

---

## 3. 依赖与环境

建议使用项目根目录虚拟环境运行：

```bash
cd /workspace/github/GunArk
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 4. 数据流程

### 4.1 拉取数据（Tushare -> Parquet）

```bash
python polars_version/fetch_kline.py
```

常用参数：

- `--start` / `--end`: 日期范围（`YYYYMMDD` 或 `today`）
- `--stocklist`: 股票清单（默认 `stocklist.csv`）
- `--exclude-boards`: 排除板块（`gem`, `star`, `bj`）
- `--out`: Parquet 输出目录（默认 `db`）

说明：

- 单票最多重试 3 次；
- 检测到疑似限流会进入冷却重试；
- 结果按 `code.parquet` 写入。

### 4.2 一键选股（推荐入口）

```bash
# 自动更新数据后，计算最新交易日
python polars_version/run.py

# 跳过数据更新，只选股
python polars_version/run.py --skip-fetch

# 计算 2026年3月 所有交易日
python polars_version/run.py 202603 --skip-fetch

# 计算月份范围
python polars_version/run.py 202601-202603 --skip-fetch

# 只跑指定策略
python polars_version/run.py --skip-fetch --strategies B1战法 暴力K战法
```

选股结果默认写入：

```text
results/polars/YYYYMMDD.json
```

---

## 5. 单日选股入口（调试友好）

```bash
python polars_version/select_stock.py --date 2026-03-12
```

常用参数：

- `--date`: 必填，`YYYY-MM-DD`
- `--data-dir`: Parquet 目录（默认 `db`）
- `--config`: 配置文件（默认 `configs.json`）
- `--tickers`: 指定股票代码列表（仅调试时建议使用）
- `--strategies`: 指定策略名列表

---

## 6. 交互式 CLI（推荐日常操作）

产品名：**日线观势**

产品简介：**A股日线级别高性能量化选股程序**

如果你希望用“菜单界面 + 上下选择 + 回车执行”，可直接使用：

```bash
python polars_version/cli.py
```

如果你希望用“全屏工作台式界面（Textual）”，可使用：

```bash
python polars_version/cli_textual.py
```

交互菜单支持：

- 最新交易日一键选股
- 月份批量选股
- 指定日期单日选股
- 仅拉取行情数据
- 多策略勾选（空格选择）
- 一键选股时先弹出“策略执行方式”对话框（默认全部，可切换指定）

说明：

- 菜单底层调用现有 `run.py / select_stock.py / fetch_kline.py`，不会改变策略逻辑；
- 若未勾选任何策略，默认按“全部启用策略”执行。

---

## 7. 配置文件说明（`configs.json`）

每个策略配置结构：

```json
{
  "class": "BBIKDJSelector",
  "alias": "B1战法",
  "activate": true,
  "_comment": "策略说明",
  "params": {}
}
```

字段约定：

- `class`: 策略类名（必须与 `Selector.py` 中类名一致）
- `alias`: 控制台展示名、结果 JSON 的策略键名
- `activate`: 是否启用
- `params`: 构造函数参数
- `_comment`: 说明字段，仅文档用途，不参与策略计算

---

## 8. 输出结果格式

`results/polars/20260312.json` 示例：

```json
{
  "B1战法": {
    "date": "2026-03-12",
    "stocks": ["000001", "600000"],
    "count": 2
  }
}
```

说明：

- `stocks` 为该策略当日选中股票代码；
- `count` 与 `stocks` 长度一致；
- 多次写入同一天会按策略键增量合并。

---

## 9. 重构与可维护性建议

如果后续继续做 clean code 演进，建议优先级如下：

1. 将 `select_stock.py` 的 Runner 与 CLI 进一步分层（core/cli）。
2. 给 `Selector.py` 的各策略补统一参数文档块（输入、窗口、阈值）。
3. 增加最小回归测试：同样输入数据下，Pandas/Polars 输出一致。
4. 对月度批量任务增加运行日志摘要（每日总数、耗时、失败数）。

---

## 10. 常见问题（FAQ）

### Q1: `run.py` 为什么默认保存到 `results/polars`？

用于给回测模块持续沉淀历史信号，避免不同版本混存。

### Q2: `configs.json` 里可以写注释吗？

标准 JSON 不支持注释。当前通过 `_comment` 字段实现“可读注释”，不会影响程序解析。

### Q3: 为什么某策略当日为 0 只？

表示当日没有股票通过该策略全部过滤条件，不是程序异常。

