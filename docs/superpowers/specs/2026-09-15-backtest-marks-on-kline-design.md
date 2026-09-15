# 回测买卖标记落到个股日K（B/S）——设计

> 前置：`2026-09-07-stock-kline-chart-design.md`（个股K线页，v2.6）、
> `2026-09-14-pagination-and-artifact-index-design.md`（产物索引与分页契约）。
> 本文只描述**增量**，不推翻既有机制。

## 1. 场景与目标

用户原话：「回测报告的时候……不是会显示个股的日K行情吗，能不能把回测的买入和卖出标记写进日K行情里，
买入写个B 卖出写个S就行」。

目标：从「看到收益数字」到「看到它在哪根K线上买、哪根上卖」——一次点击之内，不离开个股页。

## 2. 需求定稿（用户已确认项）

| 编号 | 需求 | 来源 |
|---|---|---|
| M1 | **只在从回测报告点进个股时**显示标记（URL 带 `bt=<execution_key>`）；选股页入口、直接打开个股页保持干净 | 用户确认（三选一） |
| M2 | 图上只出现 `B`/`S` 字母，不加悬停浮层 | 用户确认（「写个B 写个S就行」）|
| M3 | **十字光标弹框**里补一行成交明细（价格/股数/策略；卖出行带收益率） | 用户确认（三选一） |
| M4 | 买入 `B` 标在 K 线**下沿**、卖出 `S` 标在**上沿**；同根同向多笔显示 `B×n` | 设计（避免与蜡烛实体/影线打架） |
| M4.1 | 颜色默认 `B` 红底白字、`S` 绿底白字（**待用户复核时可改**，常量集中在 `KlineChart.tsx` 顶部） | 设计默认值 |
| M5 | 期末未平仓只出 `B`（引擎口径：有买无卖） | 设计（与 `open_positions` 数据集一致）|
| M6 | 切周期（日/周/月）、切复权、切指标，标记跟随；刷新/分享 URL 后仍在 | 设计（与既有 URL 状态一致）|
| M7 | 标记拉取失败**不得影响 K 线渲染** | 设计（既有降级哲学：辅助信息不阻塞主数据）|

## 3. 方案选型（画法）

| 方案 | 机制 | 结论 |
|---|---|---|
| **A1 自定义指标 + `text` figure** | `registerIndicator('BTMARK')`，挂 `candle_pane`，figure 直读 bar 附加字段 | ✅ **选**——与现有 `ZX`/`MT`/`ZTMACD` 同一机制；随周期/复权切换由 `calc` 自动重算；无新增生命周期 |
| A2 overlays（`createOverlay('simpleAnnotation')`） | 每个标记一个标注对象，按 `{timestamp, value}` 定位 | ❌ ① 定位要用**价格**，而图层是**前复权**坐标、成交价是**未复权**（见 §4.3）→ 老日期必然错位；② 每次切周期/复权要增删清点；③ 默认可拖拽/可删需逐个关闭 |
| A3 直接往 canvas 上画 | 订阅绘制回调自绘 | ❌ 依赖库内部实现，升级即碎 |

### 3.1 依赖事实（klinecharts 10.0.3，实测源码/d.ts）

- figure 注册表实为 `circle/line/polygon/rect/text/arc/path`（`extensions` 数组）；指标里写作 `type:'bar'`
  与 `rect` **同一条 attrs 分支处理**（本仓库 `ZTMACD` 用的就是 `bar`）。本文只用 `text`。
- **`text` figure 的 attrs 默认构造**：`{ x: 该 bar 的 x, y: yAxis.convertToPixel(当前值), text: 当前值, align:'center', baseline:'middle' }`，
  随后被 `figure.attrs?.(...)` 的返回值 **overwrite**。
  ⇒ **值必须是数字（当 y 坐标用），字母必须靠 `attrs` 覆写 `text`**；把 `"B"` 当值会让 `convertToPixel` 拿到非数字而**静默不画**（无异常、无日志）。
- 值为 `null` 的 bar 不进入绘制（`isValid(currentData[key])` 门控）⇒ 「非标记日」天然不画。
- `figures[].styles` 可用函数形式按行返回样式（`ZTMACD` 的 `macd_cross` 已用此法按 bar 改色）。
- **无 `title` 的 figure 不进图例**：图例构建处为 `if (isString(figure.title))` 才 push（lib 源码实证）；
  本仓库 `XPSD` 的 20/80 参考线已用此法 ⇒ 标记不会污染指标图例。

## 4. 后端设计

### 4.1 端点契约

```
GET /api/backtest-results/{execution_key}/marks?code=000661
```

```json
{
  "execution_key": "20260915_031528_backtest_d740308f",
  "code": "000661",
  "items": [
    {"side": "buy",  "date": "2026-08-03", "price": 78.655728, "shares": 600,
     "strategy": "single_needle_down_20", "strategy_name": "单针下20",
     "signal_date": "2026-08-03", "source": "trade",
     "profit": null, "return_pct": null},
    {"side": "sell", "date": "2026-08-04", "price": 77.154566, "shares": 600,
     "strategy": "single_needle_down_20", "strategy_name": "单针下20",
     "signal_date": "2026-08-03", "source": "trade",
     "profit": -952.82, "return_pct": -2.02}
  ]
}
```

| 状态 | 触发 |
|---|---|
| 200 `items: []` | 该回测存在，但这只票没有任何成交（含跳过的票） |
| 400 | `execution_key` 含 `/` `\` `..`（与 `/trades` 的 `_paged_dataset` 同规则） |
| 422 | `code` 不匹配 `^\d{6}$`——用 `Query(pattern=r"^\d{6}$")` 交给 FastAPI，**与 `/stocks/{code}/kline` 既有做法一致**（禁 `int`：`000001` 会被吃成 `1`） |
| 404 | 该 key 无回测产物（与 `/trades` 同语义） |
| 500 | 产物存在但读不出（`BacktestSourceUnreadable`，与既有明细分页同文案） |

### 4.2 读路径与降级

分层：新增 `presenters.backtest_marks(key, code)`（与 `backtest_rows_page` 同层、同返回约定：`None` = 无产物），
路由只做参数校验与状态码映射——不在路由里写 polars。

复用 `presenters._backtest_dataset_frame(key, dataset, bt_dir)`（**parquet 优先 → `result.json` 回落**），
与 `/trades`、`/open-positions` 完全同源 ⇒ 索引目录被删、索引读坏、产物为老格式（无 parquet）时行为一致，
不引入第二条读路径、不新增缓存。

实测（key=`20260915_031528_backtest_d740308f`）：

| 项 | 值 |
|---|---|
| `trades.parquet` 行数 | 389 |
| `filter(code == '000661')` 单次耗时 | 1.6ms（5 次均值，扫描+过滤+收集）|
| 该 run 的 `open_positions.parquet` | **不存在**（数据集为空时索引不产文件）⇒ 读路径必须按「空帧」处理，不能按「文件缺失=错误」处理 |

### 4.3 价格口径（为什么标记不按价格落点）

K 线图默认**前复权**（`apply_qfq`），而回测成交价来自本地行情库的**未复权** `open/close`
（`engine.py` 的 `entry_price = row["open"]|row["close"]`）。同一笔成交，两个坐标系里的数值可以差很多，
且越早的日期差得越多 ⇒ **标记位置只能由日期决定**，不能由成交价决定。成交价只用于弹框文本（M3）。

### 4.4 数据映射

| 数据集 | 产出 mark | 字段来源 |
|---|---|---|
| `trades` 一行 | 2 条：`buy` + `sell` | `buy_date`/`buy_price`/`sell_date`/`sell_price`/`shares`/`strategy`/`signal_date`/`profit`/`return_pct` |
| `open_positions` 一行 | 1 条：`buy`（`source="open_position"`） | `buy_date`/`entry_price`/`shares`/`strategy`/`signal_date`；`return_pct` ← `unrealized_return_pct`（浮盈，供弹框显示），`profit` ← `unrealized_pnl` |
| `skips` | 不产出（未成交没有买卖点） | — |

- `strategy_name` 由既有 `_strategy_display_name(strategy)` 补：**查不到返回 `None`**（不是回落 id）——
  与 `/trades` 端点同语义，显示层回落 `strategy_name ?? strategy` 由前端负责（已删除策略的历史报告仍可渲染）；
- 输出按 `(date, side)` 稳定排序，同序时按 `strategy`、`buy_date` 兜底（全序，便于前端归并）；
- 该端点**只读**：不建索引、不写盘、不改 `jobs`。

## 5. 前端设计

### 5.1 URL schema

`/stocks/:code?period=&adjust=&bt=<execution_key>`

- `bt` 白名单：`^[A-Za-z0-9_]{1,128}$`；非法值当作没有（并 `replaceSearchParams` 清掉，不发必 404 的请求）；
- 与 `period`/`adjust` 共存互不干扰（同一条查询串）；
- 「清除」= `replaceSearchParams((p) => { p.delete('bt'); ... })`——走 2026-09-15 修复后的写入口：
  **不经路由导航、不重挂载、不跳顶**；
- 刷新/分享后仍在（`bt` 在 URL 里，非内存态）。

### 5.2 数据流

```
StockKlinePage（挂载）
  ├─ useKline(code, period, adjust, retryKey)          ← 主数据，既有通路不动
  └─ useEffect([btKey, code]) → getBacktestMarks(btKey, code, {signal})
        成功 → marks / 失败 → marksError（只影响提示条一行，不影响图表）
```

- 取数函数进 `services/backtests.ts`（既有 `getBacktestReport` 等同僚）；响应类型进 `types/backtest.ts`；
- marks 只在 `bt` 有效**且 `code` 匹配 `^\d{6}$`** 时请求；`code` 非法时页面走既有 422 卡片，不发 marks 请求；
- **只跑一次**：依赖 `[btKey, code]`，不随 `period`/`adjust` 变化重拉——这条成立的前提是 §5.1 的写入口
  （`replaceSearchParams` 不触发重挂载，2026-09-15 已修复并有实测），若哪天退回 `setSearchParams`，
  本页会被整棵重挂载、marks 每次切周期都会被重拉；
- 切股（code 变化）时 abort 上一个请求（`AbortController` + 序号守卫，与 `useKline` 同款）。

### 5.3 映射纯函数 `attachMarks(bars, marks)`

> 规则与周期**无关**（同一条「第一根 `date >= 成交日`」在日线上退化为精确匹配、在周/月线上即分组归属），
> 故不带 `period` 形参——传了也只是死参数。前提：`bars` 按日期升序（后端 `build_kline_series` 已 `sort("date")` 保证）。

| 场景 | 规则 |
|---|---|
| 日线（统一规则退化） | 同样取**第一根 `bar.date >= 成交日`**——成交日必来自同一行情库的交易日，故等价于精确匹配；万一该日无 bar（脏信号）则顺延到下一交易日，不静默丢标记 |
| 周/月线 | 同一条规则（`aggregate_bars` 用 `date.last()` 作周期日期，已核对）⇒ 落到该周/月最后一根；同一根里的多笔**合并** |
| 同根同向多笔 | 计数显示 `B×2` / `S×3`；明细数组随行，弹框里逐笔列出 |
| 同根同时有 B 和 S | 各自独立（B 在下沿、S 在上沿），不冲突 |
| 成交日 > 最后一根 bar | 丢弃（理论上不可达：回测与K线同源） |
| 空 marks / 无 `bt` | 返回原 bars（零字段、零渲染） |

产出字段（挂在 bar 上，随行传递，供 figure 与弹框共用）：

```ts
interface BarMark { label: string; count: number; items: BacktestMark[] }
// bar.bt_buy?: BarMark | null   bar.bt_sell?: BarMark | null
```

**接入点**：`StockKlinePage` 里 `payloadWithMarks = useMemo(...)`——有 marks 时
`{ ...payload, bars: attachMarks(payload.bars, marks) }`，无 marks 时原样透传 `payload`。

**图表更新机制（实测现状，实现前必须知道）**：`KlineChart` **只在挂载时读数据**
（`setDataLoader` 的 `getBars` 由 `init` 触发，文件里没有任何 `resetData`/`updateData` 调用），
`payload` 变化本身**不会**重画；页面靠 `{!loading && !payload && …}` 的条件渲染在切周期/复权时
「卸载 → 重建」图表，所以那条路能刷新（实测：`k-line-chart-id` 由 `k_line_chart_1` 变为 `_2`，
切回日线截图逐字节复原）。

⇒ **marks 若晚于 K 线到达，仅换 `payload` 不会让 B/S 出现**。做法：给图表加数据版本 key，
数据一变就重挂载（与既有机制同构，不新增生命周期）：

```tsx
<KlineChart
  key={`${payloadWithMarks.code}-${payloadWithMarks.period}-${payloadWithMarks.adjust}-${marksRev}`}
  payload={payloadWithMarks} mainOverlays={mainOverlays} subIndicators={subIndicators}
/>
```

`marksRev` 是 marks 状态的递增版本号（无 `bt` 时恒为 0）。正常路径下 marks（1.6ms 过滤 + 本机网络）
先于 K 线到位 ⇒ 只有一次挂载、用户看不到重建；marks 慢于 K 线时才多一次重建，发生在首屏加载期。

### 5.4 图表配置

- `registerIndicator('BTMARK')`（模块级幂等，与 `ZX`/`MT`/`ZTMACD` 同款）：
  - `bt_buy`：`type:'text'`，**值 = `bar.low`**（只当坐标），`attrs` 覆写 `{ text: label, y: ... }`，`styles` 红底白字（`backgroundColor`+`padding`+圆角、`size:10`、`weight:600`）；
  - `bt_sell`：`type:'text'`，**值 = `bar.high`**，`attrs` 覆写 `{ text: label, y: ... }`，`styles` 绿底白字；
  - **下移/上移必须是像素量**：`attrs` 回调里取 `p.coordinate.current[figureKey]`（= `yAxis.convertToPixel(值)`）
    再 `±12`；写成 `值 ± 12`（价格单位）会偏出去一大截——单位不同，这是最容易写错的一处；
  - **不要用 `p.bounding` 做贴边夹取**（2026-09-15 实测踩坑）：`attrs` 拿到的 bounding 只有
    `width/height/left/right` 有意义，**`top` 与 `bottom` 恒为 0**；`Math.min(Math.max(y, top+8), bottom-8)`
    会把 y 夹成 `-8`，徽章被画到画布外（表现为「文本明明算出来了却什么都看不到」）。
    正确做法：只做有限性守卫（`Number.isFinite(y)` 才画，否则 `attrs` 返回 `null`），越界交给
    klinecharts 的窗格裁剪；
  - 两个 figure **都不写 `title`** ⇒ 不进图例（同 `XPSD` 参考线做法）；`precision` 无关（值只当坐标用）。
- 挂载/卸载：判定依据**取自 `payload.bars`**（任一 bar 带 `bt_buy`/`bt_sell`）→ `chart.createIndicator({ name:'BTMARK', paneId:'candle_pane' })`；
  清除 `bt`、该股无标记、marks 拉取失败 → 不创建（已创建则 `removeIndicator`）。
  **不新增 `hasMarks` prop**——否则「prop 说有、数据没有」会变成两份真相；
- 与 `mainOverlays`（MA/ZX/MT）共存：都在 `candle_pane`，互不影响；`cleanup` 时随图表一起销毁；
- 样式常量集中在 `KlineChart.tsx` 顶部（与 `UP`/`DOWN` 等既有常量并列），便于改色。

### 5.5 十字光标弹框（M3）

`candleLegends` 增加一行：该 bar 有 `bt_buy`/`bt_sell` 时追加

```
回测: B 78.66×600 · 单针下20        ← 买入
回测: S 77.15×600 · -2.02% · 单针下20  ← 卖出（带收益率）
```

实现形态与该函数既有返回值同形：`{ title: { text: "回测", color }, value: { text: 明细串, color } }`
（`candleLegends` 每行都是 `{title, value}` 对，不新造结构）。

明细串生成规则：同一根 bar 上**先买后卖**各一段，段内按日期序用 ` / ` 分隔，整体仍是一行
（例：`回测: B 78.66×600 · 单针下20 / S 77.15×600 · -2.02% · 单针下20`）；
无标记的 bar 不显示该行（不出现空行）。

### 5.6 信息栏提示条

个股页信息栏（既有 Alert 区，与 `adjust_degraded` 提示并列）增加一行浅色说明：

```
正在显示回测 20260915_031528_backtest_d740308f 的买卖标记（B 2 / S 2）· 查看报告 · 清除
```

| 状态 | 显示 |
|---|---|
| 加载中 | 「正在加载回测标记…」（不阻塞图表） |
| 成功且非空 | 上面那条（`查看报告` → `/backtests/{key}`；`清除` → 删 URL 的 `bt` 并清空 marks） |
| 成功但为空 | 「该回测在这只票上没有成交」（不显示「清除」以外的操作） |
| 失败 | 「回测标记加载失败：{原因}」+ `清除`；**K 线照常渲染** |

### 5.7 入口改造（M1）

`frontend/src/pages/Backtests/components/BacktestReportTables.tsx`：

- `tradeColumns` 代码列：`Link to={/stocks/${code}?bt=${execution_key}}`（`execution_key` 已在行数据里，无需新 props）；
- `openPositionColumns` 代码列：同上（保证至少有一个 B）；
- `skipColumns`：**不带** `bt`（未成交 ⇒ 无买卖点）；
- 选股结果页的代码/名称链接不动。

## 6. 错误处理

| 场景 | 行为 |
|---|---|
| marks 404（回测已被删）/ 400 / 500 / 网络错误 | 提示条一行说明 + `清除`；K 线与工具条完全不受影响 |
| `bt` 非法（格式） | 视为无标记；`replace` 清掉该参数；不发请求 |
| `bt` 指向的回测不存在 | 同 404 分支（不弹全局 `message.error`，避免误伤主流程）；**不自动改写 URL**——保留可诊断性，由用户点「清除」（自动改 URL 会让「为什么没标记」变得不可见） |
| 该股无成交 | 提示条一行「该回测在这只票上没有成交」 |
| `open_positions` 数据集为空（文件不存在） | 后端按空帧处理，不是错误 |
| marks 请求未回来就切股 | abort + 序号守卫（复用既有模式），不写过期状态 |

## 7. 测试与验收

### 7.1 后端（pytest，TDD：先 RED）

1. 200：一笔成交 → 恰好 2 条 mark（buy+sell），字段与 `trades.parquet` 一致；
2. 200：一笔未平仓 → 1 条 buy（`source="open_position"`）；
3. 200：`items: []`（该 code 无成交，且该 run 的 `open_positions.parquet` 不存在——实测该形态真实存在）；
3b. 200：同一 code 被**两个策略**各成交一笔 → 4 条 mark（2 buy + 2 sell），各自带正确 `strategy_name`；
3c. 200：同一 code 既有成交、又在 `open_positions` 里 → 卖出后的 `buy`（`source="trade"`）与未平仓的 `buy`
    （`source="open_position"`）同时在列，前端同根合并计数、弹框逐笔区分；
3d. 输出顺序稳定：`(date, side)` 为主序、`strategy`/`buy_date` 兜底，两次调用逐字一致；
4. 404：`execution_key` 无产物；
5. 400：`execution_key` 含 `..`；**422**：`code=abc123` / `code=00066`（与 §4.1 状态码表一致）；
6. 索引删除后仍正确（删除 `.index/` 后重跑用例 1）；
7. **纯读不写盘**：用一份没有 `.index/` 的临时产物目录调用端点 → 结果正确，且调用后该目录下仍无 `.index/`
   （索引由 worker 与启动预热负责，端点不得顺手建索引）。

### 7.2 前端

- `attachMarks` 纯函数断言脚本（Bun 直跑 TS）：日线命中/未命中、周线映射到当周、跨周两笔合并计数、成交日在区间外丢弃、空 marks 原样返回；
- 浏览器 E2E（真实数据，key=`20260915_031528_backtest_d740308f`，code=000661）：
  1. 从报告「交易明细」点 000661 → URL 带 `bt=` → 图上出现 `B`（2026-08-03）与 `S`（2026-08-04）；
  1b. **视图稳定**：K 线首绘后与 marks 到达后各截一次图（间隔 1.5s）比字节一致——进标记不得把视图
      重置回最新一根（`resetData` 的副作用，见 §9 风险表）；
  2. 十字光标移到该日（若该日不在默认可见区间内，先让图表滚到该 date）→ 弹框出现
     `回测: B 78.66×600 · 单针下20`；
  3. 切周线/月线 → 标记仍在（落在所属周期 bar）；切 `不复权` → 标记仍**贴在该 bar 的下沿/上沿**
     （不漂进蜡烛实体里）——证明位置只由 bar 决定、与复权无关；
  4. 点「清除」→ 标记消失、URL 无 `bt`、**页面不跳顶**；
  5. 带 `bt` 刷新 → 标记仍在；
  6. 从选股页进同一只票 → 无标记、无提示条；
  7. 人为把 `bt` 指向不存在的 key → K 线照常渲染，仅提示条一行说明。

### 7.3 实测验收记录（2026-09-15）

| 项 | 结果 |
|---|---|
| 后端用例 | 新增 12 项全绿；全量套件 **697 passed**（原 685，无回归）|
| 端点实测（key=`20260915_031528_backtest_d740308f`, code=000661）| 200，8–10ms（3 次）；买入 2026-08-03 @78.655728×600 / 卖出 2026-08-04 @77.154566×600（-2.02%），与产物逐字一致 |
| 端点负例 | 未知 key → 404；`code=abc` → 422；`abc..def` → 400 |
| 入口链接 | 交易明细行 `href=/stocks/000661?bt=<key>` ✓；跳过记录不带 bt ✓ |
| 提示条 | `正在显示回测 … 的买卖标记（B 1 / S 1）· 查看报告 · 清除` ✓ |
| **图上徽章** | 2 倍放大截图 + 视觉确认：红底白 **B** 在 K 线下沿、绿底白 **S** 在上沿 ✓ |
| 弹框行 | 纯函数 + 真实数据断言：买入日 `回测 \| B 78.66×600 · 单针下20`；卖出日 `回测 \| S 77.15×600 · -2.02% · 单针下20`；无标记日返回 `null`（不出现空行）✓ |
| 切周期/复权 | URL 保持 `bt`、计数不变（`?bt=…&period=monthly&adjust=qfq`）✓ |
| 清除 | 徽章消失（视觉确认）、提示条消失、URL 只去掉 `bt`（保留 period/adjust）、页面不重挂载 ✓ |
| 负例 | 无成交票 → 「该回测在这只票上没有成交」；不存在 key → 一行失败说明且 **K 线照常渲染**；非法 `bt` → 视为无标记并从地址栏清掉 ✓ |
| 未覆盖 | 悬停触发弹框的**实拍**（无头隐藏页 rAF 被节流，绘制帧概率性缺失）——弹框内容改由纯函数 + 真实数据断言；周/月线徽章位置由映射单测 + 切换态断言覆盖，非像素级实拍 |

### 7.4 验收标准

- 上述 E2E 全绿 + 截图存证（B/S 真的画出来了，不是「代码看着对」）；
- 后端用例全绿；
- 既有测试套件（685 项）不回退；
- 改动面：`routes/backtest.py`、`presenters.py`、`types/backtest.ts`、**`services/backtests.ts`**、
  `KlineChart.tsx`、`StockKlinePage.tsx`、`BacktestReportTables.tsx`（+ 后端 pytest 用例）。

## 8. 不做（YAGNI）

- 不做悬停浮层/自建 tooltip（M2 已定：字母 + 十字光标弹框）;
- 不做跨回测汇总、不做「最近一次回测」常驻标记（M1 已定）；
- 标记**不随报告页的策略勾选过滤**（个股页只有一只票，标记应回答「这次回测在这只票上做了什么」；
  跟着勾选走会让链接带一长串 `st`，清除与分享逻辑变复杂）。弹框里逐笔带策略名，足够消除歧义；
- 不做日 K 之外的周期专有标记（周/月线按规则合并即可）；
- 不复活 `anchor=` 定位参数（2026-09-11 已撤销）;
- 不动回测引擎、不动分页契约、不改 `jobs` 表、不加缓存层。

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| 复权口径导致标记位置错 | 位置只由**日期**决定（值取 `bar.low`/`bar.high`），成交价只进文本（§4.3） |
| `text` figure 值语义踩坑（把字母当值 → 静默不画） | §3.1 已钉死实现事实；验收含「B/S 真的出现在图上」（截图） |
| 同根多笔/多策略堆叠看不清 | 同向合并为 `B×n`；明细进弹框逐笔列出 |
| 入口链接污染（URL 变长） | `bt` 单个短参数，不带策略勾选；清除按钮一键还原 |
| 端点变成第二条读路径 | 复用 `_backtest_dataset_frame`，索引删/坏/缺失三种形态与分页端点同行为 |
| marks 晚于 K 线到达 → 仅换 `payload` 不重画（图表只在挂载时读数据） | 用数据版本 key 重挂载图表（§5.3）；两条请求同在挂载时发起、marks 更快，正常路径只挂载一次；验收含 1b 的截屏比对 |
| 图表重挂载丢失用户的缩放/滚动位置 | 标记只在首屏加载期到达；若用户已手动缩放则需要重建才显示标记——用 key 方案时「切周期」本来就会重建（现状如此），故不引入新的体验退化 |
