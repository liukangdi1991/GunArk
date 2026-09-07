# 回测「生效配置」改为随产物持久化快照

> 状态：已实施并实跑验证（2026-09-07）
> 日期：2026-09-07
> 关联：接续 `2026-09-05-trade-rule-effective-config-design.md`（该 spec 引入「读取时重算」，本篇修掉它的失真）

## 场景

2026-09-05 让报告页的「交易规则」展示**生效配置**而非请求覆盖，做法是：读取报告时把
`jobs.request_json` 喂回 `backtest_service._build_config()` 重算一遍，再 `asdict` 导出。
当时的理由是「复用 `_build_config`，引擎改默认值时快照不会失真」。

## 问题

`_build_config` 的默认值**随代码演进**，而读取时重算＝用「今天的默认值」去解释「过去跑的那笔交易」。
默认值一改，所有未显式覆盖该字段的历史报告就集体改口，和它们自己的逐笔盈亏对不上。

实证就在 2026-09-05 spec 自己的「遗留」段：`stamp_duty_rate_sell` 的服务默认曾是 `0.0001`，
commit `f9e50f99` 改成现行 `0.0005`。那份 spec 的验证 payload 钉着 `0.0001`——
即该回测的每笔卖出按 0.01% 收的印花税；可今天再打开同一份报告，`_effective_trade_rule`
重算出 `0.0005`，报告改口说按 0.05% 收。**报告与产物自相矛盾**，且无从察觉。

`fixed_hold_n_days` 同理：测试 `test_report_trade_rule_shows_effective_defaults` 跑的是
`hold=1` 的 config，却因为「没有 jobs 行 → 重算吃默认 5」而断言报告显示 5——把这条谎言钉进了测试。

根因：生效配置从未被持久化，读取时只能重算，而重算依赖的默认值是移动的。

## 决定

生效配置在 **worker 跑完时随产物固化成快照**；presenter **优先读快照**，读不到（旧产物）才回落读取时重算。

快照＝引擎真正用的那套 `BacktestConfig`，`dataclasses.asdict` 落盘，与 `metrics.json`/`result.json` 同目录。

### 为什么不按 review 建议「旧产物标未知」

review 建议读不到快照时把交易规则标成「未知」。不采纳：

- 会把**所有**历史报告的交易规则块清空，UX 回归过大；
- 旧产物的真相本就无从恢复（当时没存），回落重算**不劣于现状**——`request_json` 里用户显式设过的字段仍然准确，只有「吃了默认值」的字段可能偏移；
- 新产物起全部准确。失真面随时间自然收敛，不必为此牺牲存量报告的可读性。

## 实现

### worker：`trendradar/app/services/backtest_service.py`

`_run_backtest_worker` 写产物处（`if result_json_path:` 块）新增：

```python
Path(out_dir / "effective_config.json").write_text(
    json.dumps(asdict(config), ensure_ascii=False, default=str, indent=2),
    encoding="utf-8",
)
```

`config` 就是喂给 `BacktestEngine` 的那个对象，无需重算。

### presenter：`trendradar/interfaces/api/presenters.py`

- 抽出 `_rule_from_config_dict(cfg, request)`：从一份 config 字典（快照或 `asdict(_build_config(...))`）
  构建 `trade_rule`，字段映射与原来一致（`execution` 全量 + `capital.lot_size` + `costs` +
  `position_limits` 四键 + 请求里的 `trade_strategy`）。两路共用，避免逻辑分叉。
- `_effective_trade_rule(request, key)`：先读 `executions/{key}/backtest/effective_config.json`；
  命中→用快照；未命中→回落 `_build_config(request.get("backtest") or request)` 再 `asdict`。
- `_backtest_config(key)` 把 `key` 透传给 `_effective_trade_rule`。

`trade_strategy` 仍取自请求（它是请求数据，不随代码默认值漂移，`request_json` 里一直在）。

## 验证

- 新增/更新测试（`tests/app/test_backtest_report_fields.py`）：
  - worker 落盘 `effective_config.json` 且与 `asdict(config)` 一致；
  - 报告优先读快照：实跑 `hold=1`、jobs 行重算会得 `hold=5` 时，报告显示 **1**（真相）；
  - 旧产物无快照时回落重算：删掉快照后报告显示默认 **5**（不劣于现状）；
  - 原 `shows_effective_defaults` / `reflects_overrides_and_nested_shape` 两条改为与实跑 config 对齐。
- 全量 `.venv/bin/pytest -q`：533 passed。
- **实跑复验**（2026-09-07）：全市场回测 `20260907_101920_backtest_13508aa4`（空 dict 请求、
  走默认）落盘 `effective_config.json`，内容为引擎真正用的那套 config——
  `stamp_duty_rate_sell: 0.0005`、`mode: unlimited_cash`、`fixed_cash_per_trade: 50000`、
  `lot_size: 100`、`fixed_hold_n_days: 5`、`portfolio` 四项 null。报告端点
  `GET /api/backtest-results/{key}/report` 返回 200，`trade_rule` 取自该快照（印花税显示
  0.0005，与成交口径一致）。注：本轮请求全空 ⇒ 快照值恰等于默认值，单看报告无法区分
  「读快照」还是「重算」；区分逻辑由上面 `hold=1` vs 默认 5 的单测钉死，实跑只证快照确实落盘且可读。

## 补录（2026-09-07）：旧产物回落重算的失真不再静默

第二轮 review 通过全部修复，仅留一条无阻塞观察：**快照机制上线前的旧产物**仍走回落重算，
未显式设置的字段按今天的默认值回显，可能与实跑不符。上文「为什么不按 review 建议『旧产物标未知』」
论证了「整块标未知」回归过大（清空所有历史报告的交易规则），当时的结论是「可接受的已知取舍、无需动作」。

复查后修正这个结论：真相确实无从恢复，但「无从察觉」是可以修的——问题段自己就写着
「报告与产物自相矛盾，**且无从察觉**」。前半句无解，后半句能解。取中间档：

- **不清空**（保留回落重算的值，用户显式设过的字段仍准，报告不空白）；
- **但打标记**：回落路径给 `trade_rule` 加 `rebuilt_from_request=True`（读快照时为 `False`），
  前端据此在交易规则块顶部挂一条 warning，说明「未显式设置的参数按当前默认值回显，可能与实跑不符」。

这样新产物无任何提示（快照即真相），旧产物保留可读性的同时把失真从静默变成可见，失真面仍随时间自然收敛。

### 触点

- `presenters._effective_trade_rule`：`rebuilt = snapshot is None`，构建规则后 `rule["rebuilt_from_request"] = rebuilt`。
- `frontend/src/components/StrategySnapshots.tsx` 的 `ParamsSnapshot`：`params.rebuilt_from_request` 为真时渲染 antd `Alert`（type=warning）。
- 测试 `tests/app/test_backtest_report_fields.py`：`prefers_snapshot_over_rebuild` 断言 `rebuilt_from_request is False`；`falls_back_to_rebuild_without_snapshot` 断言 `is True`。

### 验证

全量 `.venv/bin/pytest -q`：533 passed（仅在两条既有测试上补断言，无新增用例）。
前端 `tsc -b` 无错、`vite build` 成功。
