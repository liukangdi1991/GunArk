# 未知策略/策略组 id 在提交时返回 400

日期：2026-09-04　状态：已实施并实跑验证（见文末「验证」）

## 为什么要改

`resolver.resolve()` 对请求里的 id 有三条**静默丢弃**路径：未知/停用的策略组
（`resolver.py:27`）、停用的策略（`:51`）、**未知策略 id**（`:54-56`）。前两类是正常
配置，但未知 id 是调用方写错了（拼写、策略已删除），后果是任务照跑、状态 success、
结果里只是少了一个策略——用户看到空结果会以为"今天没信号"。

实跑证据（改前，`POST /api/executions`，`strategies: ["bbi_kdj_b1", "zxdkx_balanc"]`
——后者是 `zxdkx_balance` 的拼写错误）：

- HTTP **200**，`execution_id = 20260904_081807_selection_9c0443eb`
- 任务跑完 `manifest.json` → `"status": "success"`
- `lineage.json` 的 `resolved_strategies` 里**只有 bbi_kdj_b1**，错拼的那个没有任何痕迹
  （不报错、不记日志、不进 skips）

## 决定

用户口径：「我觉得直接报400吧」。

- **未知 id = 调用方错误 → 提交时同步 400**，任务不入队。
- **disabled = 正常配置 → 不报错**，仍由 worker 里的 `resolve()` 静默跳过。
  这条分工不能混：把 disabled 也报 400 会让"临时关掉一个策略"变成不可提交。
- 校验必须发生在 **enqueue 之前**（`submit_execution_payload` 内），因为 worker 是异步的，
  等任务跑完再发现少一个策略就太晚了。

## 实现

- `domain/strategy/resolver.py` 新增 `validate_request_ids(group_defs, request)`：
  收集所有未知 group id 与未知 strategy id，一次性拼进 `ValueError`
  （`策略组不存在: ghost_group；策略不存在: no_such`）。空/None 请求直接返回。
  `resolve()` 本身**不改**——它仍是 worker 侧的静默跳过语义。
- `app/services/selection_service.py` 新增 `validate_selection_request(request, store)`：
  用 `_get_strategy_resolve_input(store)` 取 group_defs 后委托给上面的校验器。
- 三个公开 HTTP 入口全部接上（校验点在 `_strategies_names_to_ids` **之后**，
  所以显示名和 id 两种写法的错拼都能抓到）：
  1. `POST /api/executions` 选股分支（`selection_latest/single/batch`）
     → `presenters.py` 在 `sel_params` 构造后调用；已有的
     `except ValueError → 400`（`routes/executions.py:41`）负责转换。
  2. `POST /api/executions` 的 `selection_backtest` 分支
     → 内联字典提成 `bt_params` 变量后校验再提交。
  3. `POST /api/selection-backtest`（旧路由，前端已不用但仍是活的公开端点）
     → `routes/backtest.py` 加 `_store()` 辅助 + 显式
     `except ValueError → HTTPException(400)`，**不能**落进下面那个
     `except Exception → 500`。

## 影响面

- 前端**无需改动**：`apiClient.ts:23` 已经把 `payload.detail` 取出来抛成
  `ApiError(message, status)`，三个调用方（SelectionWorkspacePage /
  BacktestWorkspacePage / MarketDataPage）都 catch 后用 `messageApi.error(text)` +
  页面错误条展示。用户会直接看到「策略不存在: zxdkx_balanc」。
- 行为变化只影响**本来就写错 id 的请求**：从 200+静默少跑 变成 400+明确报错。
  正确请求（含用显示名如 `"B1战法"`）路径不变。
- `backtest_from_selection` 分支不涉及 strategies 入参（走 `selection_execution_keys`，
  已有"信号集不存在 → 400"校验），未改动。

## 验证

改后重启 :8902 实跑（`TREND_RADAR_RUNTIME_ROOT=/tmp/e2e-real`）：

| 请求 | 改前 | 改后 |
| --- | --- | --- |
| `selection_single` + `["bbi_kdj_b1","zxdkx_balanc"]` | 200，入队，success，lineage 只有 1 个策略 | **400** `策略不存在: zxdkx_balanc` |
| `selection_single` + `groups:["ghost_group"]` | 200，静默跳过 | **400** `策略组不存在: ghost_group` |
| `selection_backtest` + `["no_such"]` | 200，静默跳过 | **400** `策略不存在: no_such` |
| `selection_single` + `["B1战法"]`（显示名，正确） | 200 | **200**（未误伤） |

测试 499 passed（本决定 +12：7 个 `TestValidateRequestIds` 单测 + 5 个 API 契约测试，
覆盖三个入口的 400 与正确路径的 200）。
