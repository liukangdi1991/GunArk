# 个股 K 线图页（日/周/月 + 多副图 + 前复权）

> 状态：设计定稿，待实施
> 日期：2026-09-07
> 关联：选股工作台与回测报告的个股入口；复用 `compute_zx_lines`（`domain/strategy/formulas/zxdkx.py`）

## 1. 场景与目标

选股/回测完成后，用户需要像同花顺/通达信那样点开单只股票看走势：主图蜡烛 + 均线 +
自定义公式线，下方多个副图指标，支持日/周/月周期与前复权切换。当前系统只有选股结果
列表与回测报告表格，没有任何看图能力。

第一版明确不做：筹码分布（v2 再议）、画线工具、分钟级周期、后复权、指标参数的持久化。

## 2. 需求定稿（用户确认项）

| 项 | 结论 |
|---|---|
| 主图指标 | 蜡烛图 + MA(34/55/144/233) 四条 + 多空线/短期趋势线（`compute_zx_lines` 两线） |
| 副图指标 | VOL + MACD 默认，KDJ/RSI/BOLL 等库内置指标可加可删 |
| 周期 | 日/周/月 |
| 复权 | 前复权（默认）/ 不复权 两档 |
| 页面形态 | 独立路由页 `/stocks/:code` |
| 入口 | 选股结果页个股 + 回测报告（逐笔交易、期末持仓） |
| 筹码分布 | v2，本次不做 |

## 3. 方案选型

**选定：klinecharts 10.0.3 + 后端计算。**

- klinecharts：专业 K 线库，蜡烛图/多副图 pane/十字光标/缩放拖拽/20+ 内置指标开箱即用，
  `registerIndicator` 支持自定义线，涨红跌绿可配。通达信式交互零成本。
- 对比 echarts 6.1.0：蜡烛图 + 多 grid + dataZoom + axisPointer 联动全要手搭，工作量数倍。
- 对比 lightweight-charts 5.2.1（TradingView）：性能好但内置指标极少，中文盘习惯全 DIY。
- **计算放后端**（而非前端 JS）：复权/聚合/自定义线与选股、回测同一份 polars 代码口径
  （复用 `MarketDataStore` + `compute_zx_lines`），图表上的多空线与选股用的多空线是同一条线；
  符合「业务层一律 polars」仓库约定。库内置副图指标由 klinecharts 前端计算——纯展示，
  不构成业务决策，可接受。
- recharts 保留给既有页面；新页面 `React.lazy` 懒加载，klinecharts 不进主 bundle。

## 4. 后端设计

### 4.1 分层落点

```text
trendradar/domain/market/kline.py            # 新增，纯 polars：
                                             #   qfq 缩放、周/月聚合、随行附 zx 两线
trendradar/app/services/market_service.py    # 新增读函数 get_kline(code, period, adjust)：
                                             #   建 LocalParquetMarketStore + stock_meta 取名 +
                                             #   调 domain + 组装 payload
trendradar/interfaces/api/routes/market.py   # 新增 GET /api/market-data/kline
```

读取端点不落 `presenters.py`（已 41KB）；route→service 直调有 `submit_market_sync` 先例。
domain 纯函数独立成模块，便于 TDD。

### 4.2 API 契约

```text
GET /api/market-data/kline?code=000001&period=daily|weekly|monthly&adjust=qfq|none
→ 200 {data: {code, name, industry, period, adjust, latest_trade_date,
              bars: [{date, open, high, low, close, volume, amount,
                      zx_short, zx_long}]}}
→ 404 无该股行情数据（bars parquet 不存在或 0 行）
→ 422 参数非法（code 非 6 位数字、period/adjust 非枚举值，FastAPI Query 校验）
```

- `period` 默认 `daily`，`adjust` 默认 `qfq`
- 全量返回：实测单股 parquet 13KB / ~400 根日线，无分页必要
- `name`/`industry` 取自 `stock_meta`；查无此股时 `name` 回退为 `code`，`industry` 为 null
- `date` 为 `YYYY-MM-DD` 字符串；数值一律 float；volume/amount 保持 Tushare 原始单位
  （手/千元），展示格式化在前端
- `latest_trade_date`：返回序列最后一根的日期（周期聚合后）
- `pre_close` 不进入 v1 契约（前端涨跌幅按 4.3.4 由序列算，无需该列）

### 4.3 计算规则

1. **复权先于聚合**：逐 bar `scale = adj_factor / 最新因子`，OHLC × scale；
   volume/amount **永不缩放**。因子列缺失或最新因子 ≤ 0 时整体退化原价（与
   `_qfq_scale` 守卫语义一致：占位 1.0 已被 fetch 层硬失败拦截，此处防旧数据/测试 fixture）。
   `adjust=none` 时不缩放。
2. **周/月聚合**：按 bar 日期自然周（周一为首日）/自然月分组；
   open=组内首日 open、high=max、low=min、close=组内末日 close、
   volume/amount=sum、`date`=组内最后交易日。
3. **zx 两线随行返回**：直接调用 `compute_zx_lines(df)`（不移植 JS、不复制公式），
   参数用公式默认 (m1..m4 = 14/28/57/114)，在「返回周期对应的（复权后）close 序列」上
   计算——TDX 语义：指标随周期重算。窗口不足为 null，前端跳过。输出列名映射：
   `short_term_trend_line → zx_short`（短期趋势线）、`long_term_bull_bear_line → zx_long`（多空线）。
4. **涨跌幅口径**（前端）：复权序列相邻 bar 的 close 比值与真实涨幅数学等价（含除权日），
   因 Tushare `pre_close` 已是可比昨收。前端直接由返回序列末两根计算。

### 4.4 复权一致性测试（钉住口径）

domain 单测断言：kline 模块的 qfq 输出与 `_qfq_scale` 同语义（同 fixture 上
手工 `close × adj_factor/最新因子` 逐值相等），防止未来两处口径漂移。
`compute_zx_lines` 为直接复用（同一函数引用），无漂移面。

## 5. 前端设计

### 5.1 路由与状态

- 新增 `/stocks/:code?period=&adjust=` → `StockKlinePage`（`frontend/src/pages/Stocks/`），
  `React.lazy` + `Suspense` 懒加载，`AppRouter.tsx` 注册。
- `period`/`adjust` 同步到 URL query：刷新/分享/回退状态不丢。

### 5.2 页面结构（自上而下）

1. **信息栏**：名称 / 代码 / 行业（接口返回）+ 最新价、涨跌幅（由复权序列末两根算，见 4.3.4）。
2. **工具栏**：
   - 周期 Segmented：日 / 周 / 月
   - 复权 Segmented：前复权 / 不复权
   - 副图指标 Dropdown：VOL、MACD 默认开启；KDJ/RSI/BOLL 等内置指标可添加/移除
3. **图区**：klinecharts 容器（高度占满剩余视口）。

### 5.3 图表配置

- 样式：涨红跌绿（A 股习惯，覆盖 klinecharts 默认涨绿配色）。
- 主图叠加：
  - 内置 MA，`calcParams=[34,55,144,233]`（4 参数 → 4 条线），用户可在指标设置里改参（会话内）。
  - 自定义指标 `ZX`（`registerIndicator`）：`calc` 直接读每根 kLineData 上附带的
    `zx_short`/`zx_long` 字段返回两线值，null 跳过；两线颜色区分（短期趋势线/多空线），
    precision 2。
- 副图：VOL、MACD 默认；每个副图独立 pane，十字光标跨 pane 联动（库默认）。
- 切换周期/复权：重新请求后 `applyNewData` 整体刷新，视图回到最右端（v1 简化，
  不做可见区间保留）。

### 5.4 入口改造

- `SelectionResultPage.tsx`：个股表 code/name 格渲染为 `Link` → `/stocks/{code}`。
- `BacktestReportPage.tsx` / `BacktestReportTables.tsx`：逐笔交易表、期末持仓表的 code 格
  同样加链接。表格排序/筛选逻辑不动。

### 5.5 数据服务

- `services/marketData.ts`：`getKline(code, period, adjust)`。
- `types/kline.ts`：响应类型。

## 6. 错误处理

| 场景 | 行为 |
|---|---|
| 404（无 parquet / 0 行） | 页面级 antd Result「无该股行情数据」+ 返回按钮 |
| 网络错误 / 5xx | `message.error` + 重试按钮 |
| 加载中 | 图表区 Spin |
| 422 | 不应发生（入口均为合法 code）；发生时按错误态兜底 |

## 7. 测试与验证

**后端（TDD，先 RED 后 GREEN）**：

- `tests/domain/market/test_kline.py`：
  - 周/月聚合不变量（open=首、high=max、low=min、close=末、vol/amount=sum、date=组内末交易日）
  - 复权缩放数学（含多因子序列、因子缺失退化原价、adjust=none 不缩放）
  - 复权一致性（4.4：与 `_qfq_scale` 同语义逐值相等）
  - zx 两线 = 对同一序列直接调 `compute_zx_lines` 的结果
  - 复权先于聚合（先缩放再聚合 == 直接对缩放后序列聚合）
- `tests/interfaces/api/test_kline_api.py`（沿用 API 契约测试写法）：
  200 形状与取值、404 未知 code、422 非法参数、name 回退、默认参数。

**前端**：仓库无 JS 测试基建——`tsc -b && vite build` 通过 + 起真实页面浏览器可视验证
（周期/复权切换、副图增删、入口跳转、404 空态），作为交付证据。

**回归**：`pytest -q` 全绿（当前基线 536）。

## 8. 范围外（本次不做）

- 筹码分布（用户指定 v2）
- 画线工具、截图导出
- 分钟级周期（本地无数据）
- 后复权
- 指标参数持久化（MA/ZX 参数仅会话内生效）
- 分页/增量加载（全量足够）
- 入口：策略管理、行情数据页等其它页面的个股链接
