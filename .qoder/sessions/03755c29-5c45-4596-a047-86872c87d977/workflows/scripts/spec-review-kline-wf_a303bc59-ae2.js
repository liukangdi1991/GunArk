export const meta = {
  name: 'spec-review-kline',
  description: 'Multi-perspective adversarial review of the stock K-line chart design spec',
  phases: [
    { title: '多视角评审', detail: '6 个视角并行审 spec' },
    { title: '交叉验证', detail: '对关键发现做事实核查（仓库代码 + klinecharts 官方文档）' },
    { title: '汇总报告', detail: '合并去重、定级、产出最终 review report' },
  ],
}

const specPath = '/workspace/github/GunArk/docs/superpowers/specs/2026-09-07-stock-kline-chart-design.md'
const repoRoot = '/workspace/github/GunArk'

const sharedContext = `你是一名资深 A 股 K 线行情 Web 系统的全栈研发工程师，正在评审一份设计 spec。
- spec 路径：${specPath}（必读，全文 184 行）
- 仓库根：${repoRoot}（Python FastAPI + Polars 后端，Vite + React18 + TS + antd5 前端，四层架构 trendradar/{domain,infrastructure,app,interfaces}）
- 已实测的仓库事实（可信，但你引用相关代码时应亲自核实行号）：
  - storage/market/bars/{code}.parquet 实测列：code,date,open,high,low,close,volume,amount,adj_factor,is_suspended（无 pre_close）；每股约 397 行（2025-01-02→2026-08-21），单文件 ~13KB
  - 存量 300 个采样文件中 adj_factor 全部为 1.0（占位残留）；新同步代码 infrastructure/tushare/fetch.py:_attach_adj_factor (约103-134行) 对缺失因子硬失败
  - _qfq_scale 在 4 个策略文件重复：domain/strategy/formulas/b1.py:58、b1_v2.py:67、super_b1.py:54、ma_convergence.py:44（spec 4.4 未指明钉住哪个）
  - compute_zx_lines 在 domain/strategy/formulas/zxdkx.py:4-18，返回 (short_term_trend_line, long_term_bull_bear_line)，对传入的 close 序列做 rolling_mean，期望调用方先复权
  - 仓库无任何周/月聚合逻辑（全新增）；frontend/package.json 无 klinecharts；前端无 JS 测试基建
  - interfaces/api/routes/ 有 strategies/executions/market/backtest 四个路由文件；presenters.py ~1172行/41KB 承载 payload 组装；routes/market.py 有 submit_market_sync 的 route→service 直调先例（自行核实）
  - API 契约测试：tests/interfaces/test_api_contract.py（840行，client fixture + _write_bars helper）；pytest 基线 536
- 工作方式：先读 spec 全文，再按需读仓库代码核实，输出发现。禁止修改任何文件（只读评审）。
- 输出要求：每条 finding 含 severity（blocker=不改就无法实施或会产出错误数据 / major=上线后大概率出bug或明显返工 / minor=口径不清、文档缺陷 / nit=措辞建议）、spec 位置（章节号）、问题描述与依据（引用代码时给 file:line）、可执行的修改建议。不确定就说不确定，不要臆造；但"待核实"本身也是 minor finding。用中文。`

const schema = {
  type: 'object',
  required: ['findings', 'overall'],
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['severity', 'location', 'title', 'detail', 'recommendation'],
        properties: {
          severity: { type: 'string', enum: ['blocker', 'major', 'minor', 'nit'] },
          location: { type: 'string' },
          title: { type: 'string' },
          detail: { type: 'string' },
          recommendation: { type: 'string' },
        },
      },
    },
    overall: { type: 'string', description: '本视角对该 spec 的一句话总评' },
  },
}

const perspectives = [
  {
    key: '领域正确性',
    prompt: `视角：量化领域正确性（spec §4.2/§4.3/§4.4）。重点审：
- 前复权算法正确性：scale=adj_factor/最新因子、复权先于聚合、守卫整列退化语义是否严谨；「占位1.0已被fetch层拦截」的说法核实（读 fetch.py、storage/_adj_scan.py，检查 1.0 占位是否真被拦截还是仅拦截缺失）
- 周/月聚合口径：自然周周一首日、停牌周、月内首尾、date=组内最后交易日——与同花顺/通达信习惯是否一致；聚合后 latest_trade_date、涨跌幅口径（4.3.4 说 Tushare pre_close 已是可比昨收、前端由序列算——推演复权退化原价时除权日的涨跌幅会不会算错）
- zx 两线在周/月聚合序列上计算是否符合 TDX 语义与选股口径一致性主张
- MA(34/55/144/233) 与 known-limitation（397根日线、周线~80根）的论断是否准确
- volume 永不缩放在 A 股 qfq 语境下是否正确惯例`,
  },
  {
    key: '前端可行性',
    prompt: `视角：前端可行性 + klinecharts 集成（spec §3/§5）。重点审：
- 必须用 WebSearch/WebFetch 核实 klinecharts 10.0.3 的真实 API：applyNewData 是否存在（v9→v10 API 变化、setDataLoader？）、registerIndicator 的 registerSeries / 自定义指标 calc 返回多线图元结构、内置 MA calcParams 4参数出4条线、shouldFormatBigNumber、涨红跌绿样式覆盖的 stores/样式路径、多副图 pane 增删 API（spec 说副图"可加可删"——核实删除/添加图窗的 API 与 v1 工作量）
- React.lazy + Suspense 与 antd5/Vite6 的集成、路由参数 code 校验、URL query 同步
- 数据映射 date→timestamp 毫秒、字段命名、null 处理
- 每根 kLineData 附加 zx_short/zx_long 由自定义指标 calc 直接读——这条路在 klinecharts 类型系统下走不走得通
- 图表高度"占满剩余视口"、切换周期整体刷新丢可见区间等 UX 决定`,
  },
  {
    key: 'API契约与架构',
    prompt: `视角：API 契约设计与后端架构落点（spec §4.1/§4.2）。重点审：
- 读 interfaces/api/routes/market.py、app/services/market_service.py、domain/market/data_store.py：get_kline 的落点、route→service 直调先例是否属实、"不落 presenters.py" 的决策与仓库惯例的张力
- 契约细节：响应 {data:{...}} 包裹是否与既有端点惯例一致（查现有哪些端点包 data）；code 6位数字正则、period/adjust 枚举用 FastAPI Query 校验产生 422 的说法（核实 FastAPI 默认校验失败是 422）；latest_trade_date 语义；bars 全量返回的体积断言（400根×字段数）成立吗
- name 回退为 code、industry null 的降级路径
- 端点命名 /api/market-data/kline 是否贴切、是否该独立路由
- domain 纯函数模块划分、MarketDataStore.get_rows 复用是否足够（核实其签名与返回）`,
  },
  {
    key: '鲁棒性',
    prompt: `视角：鲁棒性与错误处理（spec §4.3/§6 + 全链路）。重点审：
- 存量 adj_factor=1.0 占位数据下的行为：qfq 静默退化为原价（守卫只在缺失/null/≤0 时触发，1.0 是合法值）——用户切"前复权"看到的其实是原价，涨跌幅/均线全错，spec 是否处理了这一场景
- 停牌 bar（is_suspended）、0 量 bar 在缩放/聚合/zx 计算中的表现
- 新股/退市股（历史不足 397 根、code 存在但 parquet 0 行、stock_meta 无此 code 的组合）→ 404 vs name 回退的分支矩阵是否完备
- 并发：线程池 JobExecutor 执行中的同步写 parquet 时读 kline 会不会读到半写文件（核实 writer 原子写 swap_in_bars）
- 性能：每股每次请求全量读 parquet + 聚合 + 4×rolling，是否需要缓存；前端 400 点渲染无压力？
- 错误处理表 §6 的 404/422/网络/5xx 之外：部分月聚合、非数字 code、大小写、空 data 的兜底`,
  },
  {
    key: '测试与验证',
    prompt: `视角：可测试性与验证计划（spec §4.4/§7）。重点审：
- 读 tests/interfaces/test_api_contract.py 的 client fixture/_write_bars：新增 tests/domain/market/、tests/interfaces/api/ 目录的说法可行性（现无这些目录、__init__.py 惯例核实）
- TDD 列表是否覆盖 §4.3 全部规则：复权先于聚合的等价性测试写法是否成立（"先缩放再聚合 == 对缩放后序列聚合"是同义反复，无法测出"顺序反了"的 bug——想想真正能测反的 fixture 长什么样：因子在周内变化）
- 536 基线、周/月聚合不变量测试、zx 一致性测试的可执行性
- 前端"tsc+build+浏览器目测"作为交付证据在本仓库无 JS 测试基建下是否够、known-limitation（zx_long 恒 null）会不会让目测验收漏掉整条线渲染 bug
- 复权一致性测试钉 _qfq_scale 的哪一个、4 份重复副本会不会漂移`,
  },
  {
    key: '可迭代性',
    prompt: `视角：范围控制与可迭代性（spec §1/§2/§8 + 演进）。重点审：
- v1 砍掉筹码/画线/分钟/后复权/参数持久化/分页——接口契约与数据结构是否给后复权（scale 语义相反）、分钟线（聚合方向反过来）、指标参数（query 传参 vs 会话）留了扩展位还是锁死了
- "全量返回无分页"在数据累积 5 年/10 年后、以及未来多股对比页复用该端点时的可持续性
- URL 状态、period/adjust query、响应包裹结构等决策对后续页面（K线弹窗嵌入回测报告、自选股列表页）的复用性
- 前端切换整体刷新丢视图、ZX 字段焊进通用 bars 结构——是否为 v2 埋雷
- spec 文档质量：需求表/决策理由/范围外清单是否自洽（§2 需求 vs §8 范围 vs 正文）、有没有实现细节该下沉到 plan 的、歧义点列表`,
  },
]

phase('多视角评审')
const reviews = await parallel(perspectives.map(p => () =>
  agent(`${sharedContext}\n\n${p.prompt}`, {
    label: `评审:${p.key}`,
    phase: '多视角评审',
    schema,
  }).then(r => r && { perspective: p.key, ...r })
))
const validReviews = reviews.filter(Boolean)
const allFindings = validReviews.flatMap(r => r.findings.map(f => ({ ...f, perspective: r.perspective })))
log(`评审完成：${validReviews.length}/6 视角，共 ${allFindings.length} 条发现（blocker ${allFindings.filter(f=>f.severity==='blocker').length} / major ${allFindings.filter(f=>f.severity==='major').length}）`)

phase('交叉验证')
const keyFindings = allFindings.filter(f => f.severity === 'blocker' || f.severity === 'major')
const verifyBatches = [
  { key: '仓库侧', tag: f => /fetch|_adj|qfq|zx|parquet|data_store|routes|presenters|test_api|writer|swap|bar|market_service|storage|b1\.py|formulas|conftest|__init__|api_contract|package\.json/i.test(f.detail + f.recommendation + f.location) },
  { key: 'klinecharts侧', tag: f => /klinecharts|applyNewData|registerIndicator|setDataLoader|calcParams|shouldFormatBigNumber|pane|图表|库|v10|v9|指标/i.test(f.detail + f.recommendation + f.location) },
]
const verdicts = await parallel(verifyBatches.map(b => () =>
  agent(`你是对抗性事实核查员。以下是针对 spec ${specPath} 的评审发现（${b.key}相关批次）。逐条验证其引用的事实是否成立：读仓库代码（${repoRoot}，给出 file:line）或用 WebSearch/WebFetch 查 klinecharts 官方文档。对每条给出 verdict：confirmed（问题真实，附证据）/ refuted（发现本身有误，说明为什么，撤回该条）/ uncertain（无法核实，降级并说明）。宁可 refuted 也不放过误报。禁止修改文件。\n\n发现列表（JSON）：\n${JSON.stringify(keyFindings.filter(b.tag).slice(0, 25), null, 1)}`, {
    label: `核查:${b.key}`,
    phase: '交叉验证',
  })
))

phase('汇总报告')
const report = await agent(`你是主笔，负责把多视角 spec 评审合并成一份最终 review report（中文，Markdown）。
被评审对象：${specPath}（个股K线图页设计 spec）。

输入一：6 个视角的原始发现（JSON）：
${JSON.stringify(allFindings, null, 1)}

输入二：两份对抗性事实核查结论（用于裁定 blocker/major 真伪）：
${JSON.stringify(verdicts.filter(Boolean), null, 1)}

要求：
1. 先重读 spec 全文，确保引用章节号准确。
2. 合并去重（同一问题多视角命中只留一条，标注也被哪些视角提出）；按核查结论裁掉 refuted 项、降级 uncertain 项。
3. 结构：总评（含推荐结论：可实施/修订后可实施/需返工，三选一给理由）→ 按严重级分组的 findings 表（编号、级别、spec位置、问题、建议、来源视角）→ 建议的 spec 修订清单（可直接照做的程度）→ 保留意见/待核实项。
4. 每条 finding 要具体到"spec 哪句话、为什么错/不清晰、改成什么"，不要空话。
5. 报告本身控制在 1200 字以内正文 + findings 表，信息密度优先。直接输出报告全文。`, { label: '汇总报告', phase: '汇总报告' })

return report