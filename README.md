# 日线观势

A 股日线级高性能量化选股系统。当前项目以 `FastAPI + React + TypeScript + Polars` 为主，CLI 入口已移除，选股、行情拉取、回测都通过 Web 工作台执行。

## 项目结构

```text
GunArk/
├── Selector.py               # 选股策略实现
├── select_stock.py           # 选股核心服务：策略加载、行情加载、预筛与精筛
├── fetch_kline.py            # Tushare 行情访问基础能力
├── configs.json              # 选股策略配置
├── stocklist.csv             # 股票基础信息缓存
├── db/                       # Parquet 行情库
├── backtest/                 # 回测引擎、交易规则、报告数据生成
├── web/                      # FastAPI 后端
├── frontend/                 # React + TypeScript 前端
├── storage/app.db            # SQLite 元数据
├── storage/cache/            # 本地交易日历等缓存
└── storage/objects/          # 选股、回测与运行日志产物
```

## 安装

```bash
pip install -r requirements.txt
cd frontend
npm install
npm run build
cd ..
```

## 启动

```bash
uvicorn web.app:app --host 0.0.0.0 --port 8000
```

浏览器访问服务暴露端口即可，例如本机直连为 `http://127.0.0.1:8000`。如果 Docker 把容器内 `8000` 映射到宿主机 `8818`，则访问 `http://localhost:8818`。

## Docker Compose 部署

推荐使用 Docker Compose 部署成一个服务容器。镜像内包含 FastAPI 后端和 React 构建产物；行情库、SQLite、运行产物、策略配置和股票列表通过宿主机目录挂载，升级镜像不会覆盖业务数据。

首次初始化并启动：

```bash
cp deploy/.env.example deploy/.env
# 编辑 deploy/.env，填写 TUSHARE_TOKEN
./scripts/install.sh
```

常用命令：

```bash
./scripts/start.sh      # 启动
./scripts/stop.sh       # 停止并删除容器
./scripts/restart.sh    # 重新构建并启动
./scripts/logs.sh       # 查看容器日志
./scripts/backup.sh     # 备份 deploy/data、configs.json、stocklist.csv
```

部署目录结构：

```text
deploy/
├── .env                 # 端口、Tushare Token、时区
├── configs.json         # 运行时选股策略配置
├── stocklist.csv        # 运行时股票基础信息缓存
└── data/
    ├── db/              # Parquet 行情库，挂载到容器 /app/db
    └── storage/         # SQLite、交易日历、选股/回测产物，挂载到容器 /app/storage
```

首次执行脚本时，如果 `deploy/data/db` 或 `deploy/data/storage` 为空，会自动从项目根目录现有的 `db/`、`storage/` 复制一份作为容器持久化数据。

默认访问地址为 `http://localhost:8818`。如需修改端口，调整 `deploy/.env` 中的 `APP_PORT` 后执行 `./scripts/restart.sh`。

## Web 功能

```text
/selections             # 选股功能区
/selections/{execution_key} # 选股结果详情
/backtests/history      # 根据选股历史回测
/backtests/selection-backtest # 选股回测
/backtests/{execution_key}  # 回测报告详情
/market-data            # 行情数据功能区
/console/{execution_id} # 运行详情与日志
```

回测功能提供两个入口：

```text
根据选股历史回测  # 选择已有 selection execution，直接读取其 selection/signals.json
选股回测          # 先按日期区间执行选股，再使用新生成的选股结果回测
```

后端 API 保持 RESTful 风格：

```text
GET    /api/strategies
POST   /api/executions
GET    /api/executions/{execution_id}
GET    /api/executions/{execution_id}/console
GET    /api/selection-results
GET    /api/selection-results/{execution_key}
DELETE /api/selection-results/{execution_key}
DELETE /api/selection-results
GET    /api/backtest-results
GET    /api/backtest-results/{execution_key}/report
DELETE /api/backtest-results/{execution_key}
DELETE /api/backtest-results
GET    /api/market-data/status
GET    /api/market-data/trading-dates
```

## 数据与产物

行情数据保存为本地 Parquet，目录为 `db/`。股票列表由行情拉取流程通过 Tushare 更新到 `stocklist.csv`。交易日历缓存保存到 `storage/cache/trading_calendar.parquet`，首次访问时按需加载或拉取。

选股和回测的元信息写入 `storage/app.db`，结构化产物写入 `storage/objects/executions/<execution_key>/`。运行详情日志写入 `storage/objects/jobs/<execution_id>/`。Web 报告页面通过后端 API 读取这些结构化数据进行渲染。

回测不再依赖 `results/signals` 兼容目录。选股信号以 artifact 形式保存在 `storage/objects/executions/<selection_execution_key>/selection/signals.json`，回测会通过选股历史的 `execution_key` 精确读取对应信号。

## 后端分层

```text
web/app.py          # FastAPI 应用组装
web/routes/         # 路由层：URL 与 HTTP 方法
web/controllers/    # 控制器层：HTTP 编排与错误转换
web/services/       # 服务层：选股、行情、回测、执行日志
web/schemas/        # 请求与响应模型
web/core/           # 配置、路径、存储基础设施
```

## 说明

`select_stock.py` 和 `fetch_kline.py` 仍保留在根目录，但它们现在是 Web 后端复用的核心模块，不再提供命令行入口。后续新增功能优先接入 Web 服务、执行日志与前端页面。
