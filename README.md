# 趋势雷达 TrendRadar

A 股日线级高性能量化选股系统。当前项目以 `FastAPI + React + TypeScript + Polars` 为主，CLI 入口已移除，选股、行情拉取、回测都通过 Web 工作台执行。

## 项目结构

```text
TrendRadar/
├── fetch_kline.py            # Tushare 行情访问基础能力
├── configs.json              # 选股策略配置
├── stocklist.csv             # 股票基础信息缓存
├── db/                       # Parquet 行情库
├── selection/                # 选股领域：指标、策略、数据加载、Runner
├── backtest/                 # 回测引擎、交易规则、报告数据生成
├── core/                     # 项目级基础设施：SQLite 元数据与 artifact 存储
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

`TUSHARE_TOKEN` 是必填项。`install/start/restart/update` 会在启动前校验该配置，未填写时直接失败，避免启动一个无法拉取行情的半可用服务。

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

## 二进制 zip 发布包

如果需要生成不依赖 Docker 的二进制发布包，先安装构建依赖：

```bash
.venv/bin/python -m pip install -r requirements-build.txt
```

然后执行：

```bash
./scripts/package_release.sh
```

脚本会先构建 React 前端，再用 PyInstaller 生成 `trend-radar` 可执行文件，并打包到：

```text
bin/trend-radar-<version>.zip
```

二进制包不包含 Dockerfile、docker-compose，也不包含 `db/`、`storage/` 运行数据。首次安装会创建运行目录、`db/`、`storage/`、`logs/`、`storage/app.db` 表结构，并生成默认 `configs.json`、`stocklist.csv`、`.env`。首次安装后的 `db/` 是空的，需要先在 Web 页面执行行情拉取，然后才能选股和回测。

首次安装：

```bash
unzip bin/trend-radar-<version>.zip
cd trend-radar-<version>
./install.sh /opt/trend-radar
cd /opt/trend-radar
vim .env
./start.sh
```

`.env` 至少需要填写：

```bash
TUSHARE_TOKEN=你的token
APP_PORT=8818
APP_HOST=0.0.0.0
```

如果缺少 `TUSHARE_TOKEN`，`./start.sh` 或直接执行 `./trend-radar` 都会拒绝启动，避免进入无法拉取行情的半可用状态。

升级安装：

```bash
cd /opt/trend-radar
./stop.sh

cd /tmp
unzip /path/to/trend-radar-<new-version>.zip
cd trend-radar-<new-version>
./install.sh /opt/trend-radar

cd /opt/trend-radar
./start.sh
```

同一个安装脚本会自动判断首次安装还是升级。目标目录里如果已经有 `.trend-radar-install`、`trend-radar` 或 `storage/app.db`，就按升级处理；否则按首次安装处理。

升级时会保留这些运行数据：

```text
db/
storage/
logs/
.env
configs.json
stocklist.csv
```

升级时会替换这些程序文件：

```text
trend-radar
_internal/
start.sh
start_background.sh
stop.sh
init.sh
configs.default.json
stocklist.default.csv
.env.example
```

当前二进制包已验证过首次安装、SQLite 初始化、缺少 Token 启动拦截、填 Token 后 Web/API 启动、升级保留数据。二进制包适用于同类 Linux x86_64 架构；Windows/macOS 需要在对应系统上重新构建。

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

回测结果依赖其使用的选股结果。已被回测引用的选股历史不能直接删除，必须先删除相关回测结果；回测结果可随时单独删除。

## 后端分层

```text
web/app.py          # FastAPI 应用组装
web/routes/         # 路由层：URL 与 HTTP 方法
web/controllers/    # 控制器层：HTTP 编排与错误转换
web/services/       # 服务层：选股、行情、回测、执行日志
web/schemas/        # 请求与响应模型
web/core/           # Web 配置、路径、全局依赖装配
core/storage.py     # 项目级 SQLite 与 artifact 存储层
selection/          # 选股指标、策略、配置加载、行情表转换、执行 Runner
```

## 说明

项目已转向 Web 工作台形态，选股、回测、行情更新均通过 FastAPI 与 React 页面执行。后续新增功能优先接入 Web 服务、执行日志与前端页面；不再维护独立 CLI 入口。
