# 趋势雷达 TrendRadar

A 股日线级高性能量化选股 + 回测系统。基于 `FastAPI + React + TypeScript + Polars`，选股、行情拉取、回测均通过 Web 工作台执行。

## 功能

- **选股**：内置 10 个"战法"策略（B1 战法、SuperB1、补票、填坑、上穿 60 放量、多空平衡、完美 B1 V2、暴力 K、倍量多空平衡等），支持单日/批量选股与策略组管理
- **行情**：通过 Tushare 增量同步 A 股日线行情（按交易日分片、限流、原子写入），本地 Parquet 存储
- **回测**：A 股交易规则引擎（涨跌停板定价、佣金/印花税/滑点、止损、持仓周期），支持基于选股历史回测与选股+回测连跑
- **执行控制台**：作业队列、进度、日志、取消

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python ≥3.11，FastAPI + uvicorn + Polars（行情与计算全部 polars，pandas 仅存在于 Tushare API 边界） |
| 前端 | Vite 6 + React 18 + TypeScript 5.8 + antd 5 + recharts |
| 存储 | SQLite（`app.db`：作业/执行元数据/策略组）、Parquet（行情与产物）、Tushare（数据源） |

## 架构

V2 采用四层架构（`trendradar/` 包）：

```text
trendradar/
├── domain/             # 纯业务层，无框架依赖
│   ├── strategy/       # 策略系统：两阶段协议（warmup/select_day）、注册表、formulas、selectors
│   ├── market/         # 行情数据接口（MarketDataStore ABC）+ LocalParquetMarketStore
│   ├── signal/         # 选股信号模型 SignalSet 与持久化
│   └── backtest/       # 回测引擎、交易执行（涨跌停定价）、组合、指标
├── infrastructure/     # 适配层
│   ├── storage/        # SQLite schema、artifact 存储、执行注册
│   ├── tushare/        # 行情同步（client/calendar/markers/rate_limit/stocklist/syncer）
│   └── filesystem/     # 原子写
├── app/                # 编排层：services（选股/行情/回测）+ jobs（JobExecutor）
└── interfaces/         # FastAPI：路由、schemas、presenters、SPA 静态托管
```

数据流：前端 `POST /api/executions` → presenter 分发 → `JobExecutor` 线程池执行 → domain 逻辑读写 Parquet/SQLite → 产物注册 → 前端轮询执行控制台查看进度与结果。

## 快速开始

### Docker Compose（推荐）

```bash
cp deploy/.env.example deploy/.env
# 编辑 deploy/.env，TUSHARE_TOKEN 必填
./scripts/install.sh      # 校验配置 + 构建镜像 + 初始化
./scripts/start.sh        # 启动（默认端口 8818）
./scripts/logs.sh         # 查看日志
./scripts/stop.sh         # 停止
./scripts/update.sh       # git pull --ff-only 后重建
./scripts/backup.sh       # 备份 deploy/.env 与 deploy/data
```

容器内运行 `uvicorn trendradar.interfaces.api.app:app`，宿主端口映射到 `${APP_PORT:-8818}`，运行数据挂载到 `deploy/data`（`TREND_RADAR_RUNTIME_ROOT=/data`）。镜像已包含前端构建产物，服务同一端口即可访问 Web 工作台。

### 本地开发

```bash
pip install -r requirements.txt -r requirements-dev.txt
cd frontend && npm install && npm run dev    # 开发服务器，/api 代理到 127.0.0.1:8000
```

```bash
python -m uvicorn trendradar.interfaces.api.app:app --host 0.0.0.0 --port 8000
python -m trendradar.cli init-v2             # 初始化存储目录与 schema（可选）
```

首次使用需先在前端"行情数据"页执行行情同步，之后才能选股与回测。

## 配置

`deploy/.env`（未跟踪，模板见 `deploy/.env.example`）：

```bash
TUSHARE_TOKEN=你的token   # 必填，缺少时启动脚本直接失败
APP_PORT=8818             # 宿主端口
TZ=Asia/Shanghai
```

代码读取的环境变量：

| 变量 | 说明 |
|---|---|
| `TREND_RADAR_RUNTIME_ROOT` / `TREND_RADAR_HOME` | 运行时根目录（默认：源码根目录） |
| `TREND_RADAR_FRONTEND_DIST` | 前端构建产物目录覆盖 |
| `TUSHARE_TOKEN` | Tushare token |
| `SYNC_INCREMENTAL_DAY_THRESHOLD` | 增量同步判断阈值（默认 20 天） |

## 存储布局

```text
storage/
├── app.db                     # SQLite：executions/artifacts/jobs/logs/策略组与设置
├── market/
│   ├── bars/{code}.parquet    # 个股日线行情
│   ├── stock_meta.parquet     # 股票基础信息
│   └── calendar.parquet       # 行情日期联合（选股/回测读取用）
├── cache/                     # 权威交易日历、同步标记（sync_done.json 等）
└── objects/executions/<key>/  # 选股/回测产物（signals.json、manifest.json、result.json 等）
```

注意两套日历：`storage/cache/trade_calendar.parquet` 为 Tushare 权威日历（同步决策用），`storage/market/calendar.parquet` 为行情日期联合（选股/回测读取用）。

## API

```text
GET    /api/strategies                    # 策略与策略组
PATCH  /api/strategies/{id}/settings      # 策略设置
POST   /api/executions                    # 提交作业（选股/回测/行情同步）
GET    /api/executions/{id}               # 作业状态
GET    /api/executions/{id}/console       # 作业日志（offset 分页）
GET    /api/selection-results             # 选股历史
GET    /api/selection-results/{key}       # 选股结果详情
DELETE /api/selection-results/{key}       # 删除选股（被回测引用时拒绝）
GET    /api/backtest-results              # 回测历史
GET    /api/backtest-results/{key}/report # 回测报告
DELETE /api/backtest-results/{key}
GET    /api/market-data/status            # 行情状态
POST   /api/market-data/sync              # 触发行情同步
GET    /api/market-data/trading-dates
```

## Web 页面

```text
/selections                    # 选股工作台
/selections/{execution_key}    # 选股结果详情
/backtests/history             # 基于选股历史回测
/backtests/selection-backtest  # 选股回测（先选股再回测）
/backtests/{execution_key}     # 回测报告
/market-data                   # 行情数据
/console/{execution_id}        # 执行控制台
```

## 测试

```bash
python3 -m pytest -q           # 全量测试（hermetic：tmp_path + 环境变量隔离，无网络/无 Docker）
```

测试镜像包结构（`tests/{interfaces,app,domain,infrastructure}/`），覆盖 API 契约、回测引擎、各选股策略（含确定性基线，保证重构前后行为等价）。

## 发布包

`bin/trendradar-2.0.0.tar.gz`（conda-pack 打包，未跟踪）包含独立 Python 环境（Python 3.11）、前端构建产物与 `trendradar-ctl` 管理脚本，适用于不依赖 Docker 的 Linux x86_64 部署。

## 文档

- 设计/实施文档：`docs/superpowers/specs/` 与 `docs/superpowers/plans/`
- 开发约定（Superpowers 工作流）：`AGENTS.md`
