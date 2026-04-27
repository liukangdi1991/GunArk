# GunArk

基于 `Polars + Numba` 的 A 股日线选股与回测项目（当前为单一实现）。

## 项目结构

```text
GunArk/
├── Selector.py               # 选股策略实现
├── select_stock.py           # 单日选股入口
├── run.py                    # 批量选股入口（可选先拉行情）
├── fetch_kline.py            # 拉取行情到 Parquet
├── data_manager.py           # 行情读写管理
├── cli.py                    # 命令行交互菜单
├── cli_textual.py            # Textual 全屏工作台
├── backtest/                 # 回测模块
├── web/                      # FastAPI + 前端工作台（routes/controllers/services/schemas 分层）
├── tools/                    # 辅助工具
├── storage/                  # SQLite 元数据与 Web 后端产物
├── db/                       # Parquet 行情库
├── data/                     # CSV 行情（可选）
└── results/signals/          # 选股结果（按交易日 JSON）
```

## 安装

```bash
pip install -r requirements.txt
```

如只跑当前主流程，也可用：

```bash
pip install -r requirements-core.txt
```

## 快速开始

```bash
# 1) 拉取行情 + 选最新交易日
python run.py

# 2) 只选股（跳过拉取）
python run.py --skip-fetch

# 3) 回测（默认全部策略）
python -m backtest --from 20260408 --to 20260415

# 4) 启动 Web 工作台
uvicorn web.app:app --host 127.0.0.1 --port 8818 --reload
```

## 选股命令

```bash
# 单日选股
python select_stock.py --date 2026-04-15

# 指定策略（空格分隔）
python run.py --skip-fetch --strategies B1战法 暴力K战法

# 月份批量
python run.py 202604 --skip-fetch

# 月份范围批量
python run.py 202601-202604 --skip-fetch
```

## 行情拉取

```bash
python fetch_kline.py --start 20190101 --end today
```

## 交互界面

```bash
# 普通交互菜单
python cli.py

# 全屏工作台
python cli_textual.py
```

## 回测说明

```bash
python -m backtest --from 20260408 --to 20260415
```

- 默认回测全部策略；可通过 `--strategy aaa,bbb` 指定一个或多个。
- 默认交易规则：`T` 日信号，`T+1` 开盘买入，`T+6` 收盘卖出（默认持有 5 个交易日）。
- 默认资金模式：`unlimited_cash`（每个信号按固定金额买入，不受账户现金约束）。
- 可切换为真实现金模式：`--mode realistic`。
- `unlimited_cash` 单票金额可配：`--cash-per-trade 50000`。
- 回测元信息写入 `storage/app.db`，结构化产物写入 `storage/objects/backtests/<run_id>/`。
- 每次回测产物为 `equity.parquet`、`trades.parquet`、`skips.parquet`、`log.txt`；报告由 Web 前端通过后端 API 渲染。
- 如果 `--to` 超过可回测上限（未来交易日不足以完成卖出），程序会直接提示可回测的最晚日期。

## Web 工作台

```bash
uvicorn web.app:app --reload
```

启动后打开 `http://127.0.0.1:8818`。Web 前端负责展示选股结果与回测报告，后端负责执行选股/回测、保存 SQLite 元信息和 Parquet 明细数据。

后端按轻量 MVC/分层组织：

```text
web/app.py                 # FastAPI 应用组装
web/routes/                # 路由层：URL 与 HTTP 方法
web/controllers/           # 控制器层：HTTP 编排与错误转换
web/services/              # 服务层：业务调用、报告数据读取
web/schemas/               # 请求模型
web/core/                  # 路径、存储等基础配置
web/static/                # 前端页面
```

当前 Web API：

```text
GET  /api/strategies                  # 选股策略列表
POST /api/selections                  # 执行选股
GET  /api/selections                  # 选股历史
GET  /api/selections/{run_id}         # 单次选股结果
POST /api/backtests                   # 执行回测
GET  /api/backtests                   # 回测历史
GET  /api/backtests/{run_id}/report   # 单次回测报告数据
```

选股结果会同时写入：

```text
storage/app.db                                      # selection_runs 与 artifacts 索引
storage/objects/selections/<run_id>/picks.parquet   # 选股明细
storage/objects/selections/<run_id>/log.txt         # 执行日志
results/signals/YYYYMMDD.json                       # 回测读取的信号文件
```

## 工具脚本

```bash
# 基准性能测试
python tools/benchmark.py --date 20260415

# 对比两个选股结果（日期或 JSON 路径）
python tools/compare_results.py 20260414 20260415

# 对比 db(parquet) 与 data(csv) 的数据一致性
python tools/compare_data.py

# 输出最新回测的 Web 报告 URL
python tools/view_report.py
```
