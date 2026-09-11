# 个股 K 线图页（日/周/月 + 多副图 + 前复权）

> 状态：v2.6——v2 吸收外部评审 37 条；v2.1 烘焙两项拍板；v2.2 处置复核 R1-R6；
> v2.3 处置二次复核 R7；v2.4 处置实现推演轮 F1-F5；v2.5 处置三核 F6；v2.6 处置
> 四核 G1——KlineSeries 构造权归 service（domain 只产 (bars, degraded)，不造占位
> 值）。关键事实已独立复现（5211/5211
> 文件 `adj_factor` 恒 1.0；klinecharts 10.0.3 d.ts 中 `applyNewData` 0 命中；
> presenters.py:3-4 与 test_api_contract.py:4-5 成文「bare, no data wrapper」；
> app.py:87/95 已注入
> `app.state.market_store`；polars 1.39.3 `dt.truncate('1w')` 锚周一、跨年周不劈分）
> 日期：2026-09-08

## 1. 场景与目标

选股/回测完成后，用户需要像同花顺/通达信那样点开单只股票看走势：主图蜡烛 + 均线 +
自定义公式线，下方多个副图指标，支持日/周/月周期与前复权切换。

**上线前置数据条件（B1，已拍板 2026-09-08：全量重建）**：盘上 5211 个 bars 文件
`adj_factor` 恒 1.0（存量落盘早于 fetch 层硬失败修复），「前复权」档当前是恒等变换。
**以全量重建（`sync/spec.py` BASELINE_START=2015-01-01）为 v1 上线前置条件**；未完成前
qfq 与 none 逐值相同，**属故障非特性**，前端以 `adjust_degraded` 显式告警（见 4.2/5.2）。
重建需要 `TUSHARE_TOKEN`。**重建完成判据**：抽验**有除权历史的票**（如 000034）
`adj_factor` 非恒 1.0——不得抽合法恒 1.0 的新股，会误判重建失败（R2）——且
`pre_close` 列落盘且非空——判据同 4.3.1 重建标志（当前 fetch 侧 cols 已含该列，
存量缺失属旧版本产物，M3 随重建消解；R7）。

## 2. 需求定稿（用户确认项）

| 项 | 结论 | 落点 |
|---|---|---|
| 主图指标 | 蜡烛图 + MA(34/55/144/233) 四条 + 多空线/短期趋势线两线 | §5.3 / §4.3.3 |
| 副图指标 | VOL + MACD 默认，库内置指标可加可删 | §5.3 |
| 周期 | 日/周/月 | §4.3.2 |
| 复权 | 前复权（默认）/ 不复权 两档 | §4.3.1 |
| 页面形态 | 独立路由页 `/stocks/:code` | §5.1 |
| 入口 | 选股结果页 + 回测报告（逐笔/持仓/skip 表） | §5.4 |
| 筹码分布 | v2，本次不做 | §8 |

## 3. 方案选型

**klinecharts 10.0.3 + 后端计算**（已核对 10.0.3 类型定义，v9 教程/API 不适用：
数据入口为 `setDataLoader`/`resetData`，无 `applyNewData`）。选型理由不变：专业 K 线库
交互零成本；对比 echarts（手搭联动）与 lightweight-charts（内置指标少）均劣；57KB gzip、
零依赖、Apache-2.0；`React.lazy` 隔离。

**计算放后端**（业务层 polars）。口径声明（M4，2026-09-08 更新）：图表与选股
**共用同一公式实现与同一前复权口径**——`compute_zx_lines_adjusted`（选股侧）
与 kline 路径（图表侧）均为 `apply_qfq` 缩放 + `compute_zx_lines`；剩余已知
差异逐条见 §4.4「已知不一致清单」。

## 4. 后端设计

### 4.1 分层落点与签名（N4/M7）

```text
trendradar/domain/market/adjust.py            # 新增：qfq 守卫与缩放（kline 专用，策略 formulas 不动）
trendradar/domain/market/kline.py             # 新增，纯 polars 周期聚合与编排
trendradar/app/services/kline_service.py      # 新增读服务
trendradar/interfaces/api/routes/stocks.py    # 新增路由（身份用 path，见 N9）
trendradar/interfaces/api/app.py              # create_app() include_router 注册（R6，漏注册=404）
trendradar/interfaces/api/schemas/market.py   # 补 KlineResponse（response_model）
```

- domain（`KlinePeriod`/`AdjustMode` 为 str Enum，定义于 `domain/market/kline.py`；
  前端 types 以 API 契约为镜像，F5）：
  - `apply_qfq(df: pl.DataFrame) -> tuple[pl.DataFrame, bool]`——守卫命中整列退化
    原价；第二返回值 = degraded（F1：bars 键集合闸门锁死 bars，标志必须走独立通道）
  - `aggregate_bars(df: pl.DataFrame, period: Literal["weekly","monthly"]) -> pl.DataFrame`
  - `build_kline_series(df, period: KlinePeriod, adjust: AdjustMode)
    -> tuple[pl.DataFrame, bool]`（bars, degraded）——编排：`sort("date")` → 0 行
    短路抛 `BarsUnavailable` → qfq → 聚合 → 附 zx 两线 → `round(4)`。
    **不返回 KlineSeries**（G1：domain 无 meta，构造含 name/industry 的对象必造
    占位值；KlineSeries 由 service 独家构造，domain 测试对 tuple 写期望，TDD 期望
    确定）
  - `class BarsUnavailable(RuntimeError)`——detail 中文，route 显式 404；
    `class MarketDataUnavailable(RuntimeError)`——detail 中文，route 显式 503（F2）
- service：`get_kline(market_store, code, period, adjust) -> KlineSeries`——
  调 `build_kline_series` 得 `(bars, degraded)` 后**独家构造**
  `KlineSeries(bars=..., adjust_degraded=..., name=..., industry=...)`（G1；
  返回域对象不带 JSON 形状；payload 组装在 route 层——与 presenters「bare, rich
  response」职责一致）。**404/503 判别机制（F2：get_rows 对缺失文件静默返空帧
  ——data_store.py:186-187 实证——不钉机制则 503 不可达）**：
  ① 先判 `market_store.bars_dir` 存在性，缺失 → 抛 `MarketDataUnavailable`（503）；
  ② 取数 `market_store.get_rows(code, date(1990,1,1), date.today())` 包 try/except，
  OSError 及 parquet 解析错误 → 抛 `MarketDataUnavailable`（503）；
  ③ 空帧 → 抛 `BarsUnavailable`（404，文件缺失与 0 行同语义）。
  stock_meta 读取**容错**：缺文件或列不全视同查无此股（name=code、industry=null），
  **不得按列直接索引**（M9：降级分支仅回 code 列，直接索引会 500）；不复用
  presenters 的 60s TTL 缓存（避免失效不同步，自读三行）。

### 4.2 API 契约（M6/M8/N1/N5/N6/N7/N10/N19/N21）

```text
GET /api/stocks/{code}/kline?period=daily|weekly|monthly&adjust=qfq|none
→ 200 裸对象（无 {"data":...} 包裹，仓库成文约定）:
  {code, name, industry, period, adjust, adjust_degraded, last_bar_date,
   bars: [{timestamp, date, open, high, low, close, pre_close,
           volume, amount, zx_short, zx_long, mt, mt_prev, mt_color,
           xpsd_short, xpsd_long, xpsig_zero, xpsig_w20, xpsig_xlong,
           xpsig_xmid, ...}]}——实现期随指标接入扩到 **22 键**
   （EXPECTED_BAR_KEYS 为唯一闸门，见 §7）
→ 404 {detail: 中文} bars 文件不存在或存在但 0 行（真无数据）
→ 503 {detail: 中文} bars 目录缺失 / 读 IO 异常（疑似整目录换名窗口，可重试）
→ 422 code 不匹配 ^\d{6}$（str + Path(pattern=...)，禁 int：000001→1）/ period|adjust
      归一化后非法（后端收 str|None，小写归一，未知值 422）

GET /api/stocks/{code}/snapshot
→ 200 {circ_mv, turnover_rate, ...}（最新交易日快照：流通市值/换手率等；
  无 token / 接口异常时字段 null；GET 无写副作用）
```

字段语义：
- `timestamp`：毫秒，**后端按 Asia/Shanghai 午夜计算**（M11+N19：前端零转换、消时区歧义）；
  `date`：`YYYY-MM-DD` 字符串（可读性）。bars 元素键集合随指标接入扩至 **22 键**
  （设计稿初版为 11 键；`EXPECTED_BAR_KEYS` 为唯一闸门，见 §7）
- `adjust_degraded`：仅 adjust=qfq 时有意义——守卫命中（含 1.0 特征守卫，仅在重建
  标志不成立时启用，判据见 4.3.1）⇒ true + 后端 warn（B1②：禁止把降级伪装成正常）；
  adjust=none 时恒 false
- `last_bar_date`：返回序列最后一根日期（**命名避开** `MarketDataStore.latest_trade_date()`
  ——全库日历语义，同名不同义，禁用（N1））
- `round(4)`：OHLC、pre_close 与 zx 两线（N5+F4，防 8.688888… 撑爆体积与 tooltip）
- nullable：**仅 zx_short/zx_long/pre_close**（前两者窗口不足；pre_close 为旧数据未
  落盘时 null，前端按 4.3.4 回退）；NaN/±Inf 一律归一为 null（N6）
- volume/amount 保持 Tushare 原始单位（手/千元），legend 必带单位（N8，§9 决策）
- 成交额不在 klinecharts 数据映射内（M12：KLineData 约定字段是 `turnover`，
  千元直塞会让 AVP 恒低 10 倍且不报错；v1 成交额只走信息栏）
- 体积：JSON 实测 64,726 B / 397 根（163 B/根，gzip 10,169 B）；2015 基线重建后
  ~2,850 根 ≈ 470KB 未压缩。交付项：`GZipMiddleware(minimum_size=1024)`（M8，
  现无任何压缩中间件）。分页触发判据（进 §8）：>3000 根或 >250KB 时再启用
  forward/backward 拉取，v1 全量
- 字段演进预案（N19）：v2 分钟线 `period=minute_5` 命名规范沿用；新增指标走
  顶层键 overlays，不复用 bars 行结构（N21）；随行指标命名 `<ind>_<series>`、可空

### 4.3 计算规则

1. **复权（先于聚合）**：逐 bar `scale = adj_factor / 最新因子`，OHLC **与 pre_close**
   × scale（qfq 档 pre_close 同步缩放保持行内量纲一致，R3；none 档原样，供交易所
   口径涨跌幅）；volume/amount **永不缩放**。**守卫清单（整列语义，任一命中 → 整列
   退化原价，禁止部分行缩放）分两组**：
   - **基础守卫（已重建/未重建两态常开）**：因子列缺失 / 列内含 null / 任一因子
     ≤ 0 或 NaN（M1：NaN 的 null_count()==0，「含 null」抓不住，须显式查）；
     相邻交易日因子比 > 3× 或 < 1/3×（B2：真因子单日变化是分红送转量级 ≤ 2×，
     越带即数据异常征兆；误判方向是退化为原价展示，安全）
   - **1.0 特征守卫（仅重建标志不成立时启用，R2/R7）**：恒 1.0；列内同时含 1.0 与
     非 1.0。**重建标志 = 文件含 pre_close 列且其 null_count ≤ 1**（R7：仅判「含列」
     会被重建前的常规增量同步击穿——`_align_columns` 把旧行 pre_close 补 null、新行
     带真值，合并文件从此「含列」且 adj_factor 恰成混合列，门控被击穿后 F ≤ 3 的
     股票假跳空无告警；null_count ≤ 1 与「正常重建文件至多首行 1 个 null」干净
     分离，对齐补 null 的旧行数以百计；阈值失效方向是误报 degraded（可见），
     不是漏报）。已重建后「恒 1.0」是合法形态（上市从未除权的新股）、「1.0→非 1.0」
     也是合法形态（Tushare 因子为累计绝对值，上市即 1.0、除权后抬升）——两规则
     不门控会在重建后把全市场除权股误降级；未重建的存量库（因子恒 1.0 或增量
     混合）仍由这两条拦住
   - 守卫命中 ⇒ `adjust_degraded=true` + `logging.warning`
   - `adjust=none` 时不缩放
   - **入口不变量**：`build_kline_series` 先 `sort("date")`（「最新因子」=按日期末位，
     load_bars/get_rows 不保证序）+ 0 行短路抛 `BarsUnavailable`（M2）
2. **周/月聚合（M16）**：周键 `dt.truncate('1w')`（已验证锚周一、跨年周不劈分）、
   月键 `dt.truncate('1mo')`；**明令禁止 `(year, week)` 分组**（跨年 ISO 周劈两根）。
   open=组内首日 open、high=max、low=min、close=组内末日 close、
   pre_close=组内首日 pre_close（qfq 档取缩放后值，可比昨收，供 none 档涨跌幅）、
   volume/amount=sum、
   `date`/`timestamp`=组内最后交易日。整周/整月停牌（组内无 bar）不产出该周期 bar。
   **停牌判据 = bar 缺失**；`is_suspended` 恒 false 占位列，不消费、不补齐；
   列访问一律容错（N3：本地文件与空帧列集不一致）
3. **zx 两线随行**：在返回周期对应（复权后）close 序列上计算——指标随周期重算。
   - **多空线（zx_long）**：直接复用 `compute_zx_lines(df)` 的
     `long_term_bull_bear_line`（窗口 14/28/57/114 的 MA 均值，与选股同源），
     参数用公式默认。窗口不足为 null。
   - **短期趋势线（zx_short）**：用户 TDX 原文公式 **EMA(EMA(C,10),10)**
     （Y=(2X+9Y')/11 ⟺ polars `ewm_mean(alpha=2/11, adjust=False)` 双重平滑），
     实证 300274@2026-09-07 = 101.9066 ≈ 通达信 101.91。**与策略侧
     `short_term_trend_line` 已对齐**（2026-09-08 用户拍板策略口径同用 TDX 双重
     EMA，§4.4 清单已销）。EMA 自首根收敛，
     无 null 预热。**ZX 参数 v1 不可调**（M17）
4. **涨跌幅（前端）**：qfq 档由序列相邻 close 比值计算（等价性来自因子比相消
   f_t/f_{t−1} = c_{t−1}/pre_close_t）。**none 档**优先 (close−pre_close)/pre_close
   （交易所口径，除权日正确）；pre_close 为 null（旧数据未落盘）时回退序列比，**仅该
   回退路径存在已知偏差**（10 送 10 显约 −50%），回填后消解。序列长度 <2 时涨跌幅为
   null，前端显示「—」（N17）
5. **known-limitation 双状态表（M15）**：

   | 周期 | 当前快照（397 日 / 85 周 / 20 月） | 2015 基线重建后 |
   |---|---|---|
   | 日线 | zx_long 需 ≥114 根、MA233 需 ≥233 根才有首值 | 基本全可见 |
   | 周线 | zx_long 恒 null；MA144/233 恒 null | 全可见（~600 周） |
   | 月线 | MA34/55/144/233 **全部** null；zx_short 仅 ~7 点；zx_long 恒 null | MA34/55、zx_short（~2016-03）、zx_long（~2024-07）可见；**MA144 自 ~2026-12、MA233 自 ~2034-05 恒 null（重建不改变，数据累积属性，R4/R7-nit）** |

   限制随重建**自动缓解**，前端不得写死 null 断言；「月线页不得为空白图」列入验收

### 4.4 口径参照与已知不一致清单（M2/M4）

- **qfq 参照实现**：`domain/market/adjust.py` 新实现，语义**钉住 b1.py:58
  `_qfq_scale`** 为参照副本（4 份副本函数体逐行相同但 docstring 有差，不宣称收敛）；
  4 份策略内副本收敛为共享 helper 记入 §8（本次不动，有确定性基线的测试在）
- **等价域**：§7 的「与参照同语义逐值相等」断言限定在**因子列无 null 且全 > 0**
  的 fixture 上；kline 守卫**有意更严**（列内 null 整列退化 vs 参照逐行跳过），
  是已知且被测试钉住的差异
- **已知不一致清单**（共用公式实现 ≠ 同一条线的完整保证）：
  - ~~图表短期趋势线 ≠ 策略侧短期线~~（**2026-09-08 已对齐**：用户拍板策略口径
    对齐 TDX 原文公式——`compute_zx_lines` 短期线由 MA(C,14) 改为
    EMA(EMA(C,10),10)，图表与 5 个 selector 消费同一实现；多空线公式本就一致。
    影响面：zxdkx_balance / volume_spike_balance 两策略的 stick 信号语义更新，
  - ~~5 个在册 selector 以未复权 close 喂 `compute_zx_lines`~~（**2026-09-08 已对齐**：
    5 个 selector 全部切换 `compute_zx_lines_adjusted`（apply_qfq 缩放 +
    compute_zx_lines，与图表路径同一公式同一口径）；确定性基线经复核未变
    （fixtures 无 adj_factor 列 → 守卫退化原价路径，历史行为不变））
  - 预热差异：图表多空线 null（min_samples=window）、图表短期线 EMA 自首根
    收敛无 null，vs b1.py:93 等 min_samples=1
  - 多空线与通达信存在 ~0.04（0.035%）残差：TDX 前复权数据与 Tushare
    adj_factor 的因子精度差异所致，属数据源精度差，不收敛
  - 回测 engine.py:504-511 是第三份手写重算，不在本 feature 收敛范围

## 5. 前端设计

### 5.1 路由与状态（B3/M10/M13/M18/N10/N11/N18）

- `/stocks/:code?period=&adjust=` → `StockKlinePage`；route element 内包 `<Suspense>`
  （React.lazy）；URL `period/adjust` 白名单归一，非法值 replace 修正（不发必 422 的
  请求）；加载前 `^\d{6}$` 本地校验 `code`
- **URL schema 现在定全**：预留 `&anchor=YYYY-MM-DD`（初次定位到该日期，实现可后置；
  §5.4 两处 Link 带当笔日期）；v1 不做可见区间保留，仅支持初次定位
- 组件三层（N18）：`<KlineChart>` 纯展示 / `useKline(code, period, adjust)` 取数 +
  abort + 序号守卫 / Page 只做 URL↔props。用户切换 `navigate(..., {replace:true})`
  不刷历史栈；浏览器回退触发重取。不做内存缓存（本地毫秒级）
- AppShell 菜单归属：`/stocks/*` 增加 selectedKey 分支，不高亮「选股」（N11）

### 5.2 页面结构（N2/N13）

1. **信息栏**：名称/代码/行业 + 最新价、涨跌幅（跟随当前周期，见 4.3.4）+
   **固定「数据截至 {last_bar_date}」**（库止 2026-08-21，落后多日，「最新价」会被
   当现价）；`adjust_degraded=true` 时显示 Alert「复权因子缺失，前复权退化为原价，
   需重建行情数据」（禁止静默回显请求参数）
2. **工具栏**：周期 Segmented（日/周/月）、复权 Segmented（前复权/不复权）、
   副图指标 Dropdown（`createIndicator/removeIndicator`，勾选态反查 `getIndicators`；
   无 addPane/removePane，空窗自动销毁）
3. **图区 CSS**：页面 flex column，图区 `flex:1; min-height:0`（父级无确定高度时
   height:100% 解析为 0）；≤720px 降级固定高（N13）

### 5.3 图表配置（v10 真实机制，B3/M10/M11/M12/M14/N12）

- **数据通路（单一）**：`useKline` 的 useEffect([code,period,adjust]) 发 GET
  （AbortController：新请求 abort 上一个 + requestId 序号守卫，AbortError 静默）→
  payload 写 ref → `chart.resetData()`。`setDataLoader` 只注册一次：
  ```ts
  chart.setDataLoader({
    getBars: ({ type }, done) => {
      if (type !== 'init') { done([], { forward: false, backward: false }); return }
      done(toKlineData(dataRef.current), { forward: false, backward: false })  // 显式传 more
    },
  })
  ```
  错误态（404/网络）由 React 持有并渲染，loader 不发请求、不进 loading 死循环；
  切换周期顺序钉死：**取数写 ref → `resetData()` → `setPeriod()`**（v10 中
  setSymbol/setPeriod 会触发 getBars——顺序反了会先画出「旧数据+新轴」瞬态，R5）；
  实施时若实测 setPeriod 对后端聚合序列无轴增益（timestamp 已定轴位）可省略，
  验收以「切周期无旧数据瞬闪」目测项为准
- **timezone**：`init(el, { timezone: 'Asia/Shanghai' })` + timestamp 后端算好
  （裸 `Date.parse('2025-01-02')` 是 UTC 午夜，NY 时区实测渲染 01-01 整条错位一天）
- **样式（涨红跌绿）**：两套样式路径都要覆盖——蜡烛（candle.bar：涨/跌色的实体、
  影线、边框）与指标量柱（indicator.bars），实施时对照 10.0.3 `Styles` 类型逐项
  核对，验收含「VOL 柱色与蜡烛一致」目测项
- **主图叠加**：内置 MA `calcParams=[34,55,144,233]`（会话内可改参，M17 拆行）；
  自定义 `ZX`（registerIndicator，series:'price'，precision 2，figures 两线
  `zx_short`/`zx_long` 直读 bar 附加字段，null 跳过，两线颜色区分）
- **量纲/格式**：VOL/金额 legend 必带单位（手/千元）；`shouldFormatBigNumber`
  默认缩写是 K/M/B，需自定义 formatter 为万/亿；locale 设 zh-CN
- **生命周期（M14）**：`init` 在 effect 内，cleanup 调销毁 API（名字以 10.0.3 d.ts
  为准），禁模块级缓存实例；resize v10 内置（ResizeObserver）免手工；
  main.tsx 全站 StrictMode 双挂载——验收「dev 下不出现两张画布」

### 5.4 入口改造（N11）

- `SelectionResultPage.tsx`：code 列渲染 `Link`（name 列同步）；回测侧三张表全加：
  逐笔交易、期末持仓（`BacktestReportTables.tsx`）、**skipColumns 跳过票表**
  （跳过票恰是最想复盘的）；链接带 `&anchor={当笔日期}`（M18；**2026-09-11 撤销**：
  anchor 参数零消费已从链接剥除，定位属后置需求）；表格排序/筛选不动
### 5.5 数据服务

- `services/marketData.ts`：`getKline(code, period, adjust, {signal})`——复用
  apiClient 的 ApiError.status 与 15s 超时（M13）
- `types/kline.ts`：响应类型（文件头标 spec 版本，N21）

## 6. 错误处理（按 ApiError.status 分支，N7/N10/M13）

| 场景 | 行为 |
|---|---|
| 404（文件缺失/0 行） | 页面级 Result「无该股行情数据」+ 返回 |
| 503（目录缺失/IO） | 「行情数据正在更新，请稍后重试」+ 重试 |
| 422 | 归一化失败兜底（正常操作不可达：URL 已白名单化） |
| 超时 / 网络错误 | message.error + 重试按钮 |
| Abort | 静默（新请求已接管） |
| 重试/加载期间 | 工具栏切换禁用，防乱序覆盖 |
| 极短历史（<2 根） | 正常渲染，涨跌幅显示「—」（N17） |

## 7. 测试与验证（TDD，先 RED 后 GREEN）

**验收前置数据条件（B1）**：测试 fixture 必须**非平凡因子**（至少一段因子阶跃，
如 `[1.0]*n + [1.5]`）；现有 `test_api_contract.py` 的 `adj_factor=1.0` fixture
测不出缩放分支，kline 测试不得沿用。`storage/` 不入 git，重建后基线口径在交付
文档显式声明。

- `tests/domain/market/test_kline.py`（目录与 tests/domain 既有惯例对齐）：
  - **判别式断言（M5）**：qfq 输出与 none 输出**不全等** + 末根 close 逐值相等
    （拦「qfq≡none」与「取错缩放基准」两类实现错误）；「复权先于聚合」用
    **组内含因子阶跃**的 fixture（跨除权日落在同一自然周）＋字面量 oracle + 负控
    （反向实现必不通过）；zx 断言钉输入：同一序列喂 raw close 与 qfq close 的
    结果**必须不同**、两线 fixture 互不相等（防交换）
  - 聚合不变量 + 边界夹具（M16/N3）：跨年周（2025-12-29~2026-01-02 一根）、
    长假单日周棒、首/尾残周、`Σ周volume == Σ日volume` 守恒、停牌整周缺口、
    末根早于市场最新日的停牌股
  - 复权守卫分两组（B2/M1/R2）：基础守卫（两态常开）列内 null / ≤0 / NaN /
    相邻比越带 → 退化；1.0 特征守卫（仅未重建——pre_close 列缺失）恒 1.0 →
    degraded、`[1.0]*n+[1.5]` 混合 → 退化；**已重建（有 pre_close 列）时 1.0 特征
    守卫不触发**：恒 1.0 → 正常、`[1.0]*k+[2.0]*m`（合法除权形态）→ 正常缩放
    （前段 ×0.5、后段 ×1）；不变量「qfq 末根 OHLC == 原价末根」（M1）；qfq 一致性
    限定在无 null 全 >0 等价域（4.4）
  - 缩放数学 oracle（R1 修正——换非混合 fixture）：`[1.2]*n+[1.5]`（全 >0、无 1.0、
    相邻比 1.25 不越带，守卫全过）→ 前段 ×1.2/1.5、末段 ×1（手写字面量，勿自比；
    钉死「分母 = 最新因子」，拦取错基准的实现）
  - 重建标志穿透用例（R7）：旧文件（无 pre_close 列）经一轮 `_align_columns` 式
    增量合并（旧行 pre_close=null、新行真因子 F）→ 标志判「未重建」→ 1.0 特征
    守卫仍触发、degraded=true（现有 fixture 是「列整体缺失」，此状态原清单未盖到）
- `tests/interfaces/test_kline_api.py`（模块内私有 helper，随现有目录惯例）：
  200 形状 + **bars 键集合精确等于 EXPECTED_BAR_KEYS（22 键）**（唯一闸门）+ `type(close) is float`
  + 严格 JSON 可解析（NaN→null，N6）；404（空帧）/503（目录缺失、读异常两 fixture，
  F2 机制）/422（Path 校验，F3）分支；adjust_degraded 顶层字段断言（degraded
  fixture vs 正常 fixture，F1 落钉后可写）；meta 缺文件/缺列降级（M9）；2 行极短
  历史（N17）；pre_close 缺失（null）回退路径（4.3.4 残余偏差仅限此路径）；
  date 字符串化/timestamp 值钉在 HTTP 层（N15）
- **前端**（不引 vitest，§9 决策）：`tsc -b && vite build` + 浏览器可判验收：
  StrictMode 双挂载无双画布（M14）；系统时区改 America/New_York 轴日期不偏移
  （M11）；**最右轴日期 == last_bar_date**；VOL 柱色与蜡烛一致（N12）；
  周期/复权/副图增删/三处入口跳转/**anchor 定位（v1 撤销，见 §5.4 注记）**/404·503 空态/Alert 告警态/
  切周期无旧数据瞬闪（R5）

**上线前置（B1，✅ 已完成 2026-09-08）**：行情数据 2015 基线全量重建已执行
（5468 只，staging→swap_in_bars 原子换名），判据通过：000034 因子 13 个 distinct
（非恒 1.0）+ pre_close 列零空值落盘；`adjust_degraded` 已回 false。过程中修复
全量拉取限速缺陷（一票两次调用只扣 1 桶令牌，`cae0db2e`）并确认入账 7 个
2015-07 股灾停牌周 doubtful 日。**selector 未复权输入口径对齐亦已完成**
（5 个 zx 系 selector 切换 `compute_zx_lines_adjusted`，见 §4.4）；4 份
`_qfq_scale` 副本收敛仍为独立后续作业。
范围外：筹码分布（v2）、画线工具/截图、分钟级、后复权、MA 参数持久化、
盘中刷新/实时推送、多股同栏、可见区间保留（anchor 仅初次定位）、vitest（§9）、
非交易日坐标空洞（数据驱动，自然结果）。
**分页触发判据**：>3000 根或 >250KB 启用 forward/backward（M8，当前不满足）。

## 9. 歧义与待确认

**已定决策**（默认值，用户可推翻）：

| 议题 | 决策 |
|---|---|
| 响应包裹 | 裸对象（M6，仓库成文约定压倒直觉） |
| 量纲 | 保留手/千元 + legend 带单位（归一元/股更干净但与行情软件习惯脱钩，N8） |
| none 档除权日涨跌幅 | pre_close 随重建回填，none 档用交易所口径；null 回退序列比（M3，已拍板不接受偏差） |
| vitest | v1 不引入，用可判验收项替代（N16） |
| meta 读取 | service 容错自读，不复用 60s TTL 缓存（M9） |
| skipColumns | 加链接（N11） |
| ZX 参数 | v1 不可调（M17） |
| KlineSeries 形状 | dataclass(bars, adjust_degraded, name, industry)，**由 service 独家构造**；domain 只产 (bars, degraded) 元组（F1 通道 + F6 交付 + G1 构造权，不造占位值） |
| 1.0 特征守卫门控 | 重建标志 = pre_close 列存在且 null_count ≤ 1；标志不成立才启用恒 1.0/混合守卫（R2 扩展 + R7 收紧：防增量同步 _align_columns 补 null 击穿门控） |

**待用户拍板**：（无——两项已于 2026-09-08 拍板：①B1 前置=全量重建（A 方案，
用户提供 `TUSHARE_TOKEN` 后执行）；②M3 不接受偏差，pre_close 一并回填）
