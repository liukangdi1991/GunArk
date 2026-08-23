# Post-Refactor Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close out the cleanup items left over from the V2 clean rebuild: stale README, stale `deploy/data/` runtime, duplicate `perfect_b1_volume_stepdown` strategy, duplicate `MarketDataStore` Protocol in the backtest engine (review #9), and pandas at the Tushare boundary (review #10).

**Baseline (must stay green at every phase):** `cd /root/workspace/repo/GunArk && python3 -m pytest -q -p no:cacheprovider` → **258 passed** on system Python 3.13.9 (pytest 8.4.2). Never write `.pytest_cache` (`-p no:cacheprovider`).

**Reference:** `.superpowers/sdd/review-v2-overall.md` (issues #9, #10), `docs/superpowers/specs/2026-07-09-trendradar-v2-clean-rebuild-design.md`.

---

## Decisions needed from user (BEFORE execution)

1. **`deploy/data/` deletion scope (Phase 4 — destructive).** The whole dir is gitignored local runtime. Must approve: stop any running service → backup → delete stale `db/` (5495 legacy parquet), `storage/app.db` (V1 schema), `configs.json`, `stocklist.csv`, `logs/` → re-init on next start.
2. **`perfect_b1_volume_stepdown` (Phase 2): Option A delete registration (recommended) vs Option B re-implement volume-step-down as a new feature (needs its own spec; recommend deferring).**
3. **Tushare-boundary pandas removal (Phase 5): in scope (recommended, small) vs defer.**

---

## Phase 1: README rewrite (S)

**Files:**
- Modify: `README.md`

- [x] Rewrite `README.md` to describe the ACTUAL V2 system (current README describes the removed V1 layout). Required content:
  - Overview: A-share daily-bar quant selection + backtest, FastAPI + React + TypeScript + Polars.
  - Architecture: 4 layers (`trendradar/domain`, `app`, `interfaces`, `infrastructure`), strategy protocol (warmup/select_day), storage layout (`storage/market/bars/{code}.parquet`, `storage/objects/executions/<key>/`, `storage/app.db`, `storage/cache/`).
  - Entry points: Docker (`uvicorn trendradar.interfaces.api.app:app`, compose maps `8818->8000`), `scripts/*.sh` (install/start/stop/restart/update/logs/backup), `cli.py init-v2`.
  - API routes (`/api/strategies`, `/api/executions`, `/api/selection-results`, `/api/backtest-results`, `/api/market-data/*`), frontend routes (`/selections`, `/backtests/*`, `/market-data`, `/console/:id`).
  - Testing: `python3 -m pytest -q` (hermetic, ~258 tests), dev deps in `requirements-dev.txt`.
  - Release: conda-pack bundle in `bin/trendradar-2.0.0/` (untracked), ops model.
  - Configuration: `deploy/.env` (`TUSHARE_TOKEN` required, `APP_PORT`, `TZ`), env vars read by code (`TREND_RADAR_RUNTIME_ROOT`, `TREND_RADAR_HOME`, `TREND_RADAR_FRONTEND_DIST`, `TUSHARE_TOKEN`, `SYNC_INCREMENTAL_DAY_THRESHOLD`).
- [x] Acceptance: README contains NO references to removed V1 artifacts — `web/`, `selection/`, `backtest/`, `core/`, root `db/`, root `configs.json`, `stocklist.csv`, PyInstaller zip, `requirements-build.txt`, `fetch_kline.py`, "CLI 入口已移除".
- [x] Gate: docs-only; `pytest` still green (no code touched).

## Phase 2: `perfect_b1_volume_stepdown` duplicate strategy (S) — pending decision (A recommended)

**Files:**
- Modify: `trendradar/domain/strategy/selectors/__init__.py`

- [x] Option A (recommended): delete the `register(StrategyDefinition(strategy_id="perfect_b1_volume_stepdown", ...))` block (lines ~70-76). `perfect_b1_v2` stays.
- [x] Verified no other live references to delete: `grep -rn "perfect_b1_volume_stepdown" trendradar/ frontend/src/ tests/` must return nothing (registry/resolver are generic by id; frontend strategy list is API-driven; tests use `perfect_b1_v2` only). Dated spec/plan docs under `docs/superpowers/` keep the id as a historical record — do NOT edit them.
- [x] Optional ops note (not code): existing V2 DBs may hold `strategy_settings`/`strategy_group_members` rows for the deleted id; resolver skips unregistered ids (existing behavior), so reads stay safe. Prune rows only if the operator wants a clean DB.
- [x] Option B (deferred feature): re-implement volume-step-down logic as a real selector — requires a new spec; do not do it in this cleanup.
- [x] Gate: `pytest` green; `register_all()` yields 9 strategies; default group seeding (fresh DB) contains no `perfect_b1_volume_stepdown`.
- [x] Risk: stored `signals.json` snapshots carry `strategy_id` as data only — historical artifact reads are unaffected by registration removal.

## Phase 3: Consolidate duplicate `MarketDataStore` in engine (S) — review #9

**Files:**
- Modify: `trendradar/domain/backtest/engine.py`

- [x] Delete the local `class MarketDataStore(Protocol)` (lines ~21-25) and replace with `from trendradar.domain.market.data_store import MarketDataStore`. The shared ABC covers all 4 engine methods; return-type mismatch on `get_rows` is already handled by `engine._to_rows` (`isinstance(data, pl.DataFrame) → data.to_dicts()`), and `LocalParquetMarketStore.get_rows` returns `pl.DataFrame`. Test fakes (`tests/domain/test_backtest_engine.py::FakeMarketStore`) are plain classes — no ABC enforcement at runtime, no test change needed.
- [x] Update any remaining type annotations in engine that referenced the local Protocol (they now resolve to the imported ABC).
- [x] Acceptance: `grep -rn "class MarketDataStore" trendradar/` returns exactly one definition (`domain/market/data_store.py`); `pytest` green.
- [x] Gate: `python3 -m pytest tests/domain/test_backtest_engine.py tests/domain/test_backtest_portfolio.py tests/domain/test_backtest_execution.py -q` then full suite.

## Phase 4: `deploy/data/` stale runtime cleanup (M) — DESTRUCTIVE, pending approval

**Context (verified):** `deploy/data/` is fully gitignored (`.gitignore` lines 12-16: `deploy/.env`, `deploy/configs.json`, `deploy/stocklist.csv`, `deploy/data/`, `deploy/backups/`). Only `deploy/.env.example` is tracked. Contents: `configs.json`, `db/` (5495 legacy parquet), `logs/`, `stocklist.csv`, `storage/` (V1-schema `app.db` with tables `executions, sqlite_sequence, selection_results, backtest_results, execution_items, execution_item_params, execution_item_metrics, artifacts, backtest_selection_links, execution_log_links` — none match current `trendradar/infrastructure/storage/schema.py` DDL; plus `cache/`, `objects/`).

- [x] Step 0 — verify no live service: `docker compose ps` and `pgrep -f "uvicorn|trendradar"`. If running, STOP it (user-visible action; coordinate with user — this is their deploy env) before touching anything.
- [x] Step 1 — backup: `tar -czf deploy/backups/data-$(date +%Y%m%d-%H%M%S).tar.gz -C deploy data` (target dir is gitignored; note `deploy/backups/` may need `mkdir -p`).
- [x] Step 2 — remove stale V1 artifacts under `deploy/data/`: `db/`, `storage/app.db`, `configs.json`, `stocklist.csv`, `logs/`. Keep (or clear) `storage/cache/` and `storage/objects/` per user preference — default: remove too (V1-era contents), keep the directory skeleton.
- [x] Step 3 — re-init on next start: `scripts/install.sh`/`start.sh` recreate the layout (`ensure_deploy_layout`) and run `init_schema` in-container; or `python -m trendradar.cli init-v2` locally. Confirm fresh `app.db` contains ONLY current-schema tables (`executions, execution_items, artifacts, execution_links, jobs, job_logs, strategy_groups, strategy_group_members, strategy_settings, market_sync_runs`).
- [x] Acceptance: service starts, `GET /api/market-data/status` returns 200, old tables absent.
- [x] Risk: deleting while a service writes (mitigated by stop-first + backup); accidental data loss (mitigated by backup tarball).

## Phase 5: Tushare-boundary pandas removal (S) — review #10, pending decision

**Files:**
- Modify: `trendradar/infrastructure/tushare/client.py`, `calendar.py`, `stocklist.py`, `syncer.py`, `requirements.txt`

Current pandas touch-points (verified): `client.py` `validate_token` (`result.empty`), `calendar.py` `fetch_trade_calendar` (`resp.empty`; already avoids `pl.from_pandas`), `stocklist.py` (`data.empty`, `pl.from_pandas(data)`), `syncer.py` (`import pandas as pd`; `_response_to_df` + `_fetch_daily_by_date` use `resp.empty`/`pl.from_pandas`).

- [x] Replace every `pl.from_pandas(x)` with `pl.DataFrame(x.to_dict(orient="list"))` — the exact pattern `calendar.py` already documents as avoiding pyarrow issues.
- [x] Replace every `.empty` check on Tushare responses with `not resp.to_dict(orient="list")` (empty frame → empty dict).
- [x] `syncer.py`: drop `import pandas as pd`; loosen `_response_to_df(resp: pd.DataFrame, ...)` annotation to accept any object with `to_dict` (or plain `resp`) — tests keep passing pandas DataFrames as API mocks, which is correct boundary simulation.
- [x] `requirements.txt`: remove `pandas>=2.0.0` (tushare pulls pandas transitively, so the runtime env is unchanged; the manifest becomes honest about OUR dependency).
- [x] Tests: unchanged (they mock Tushare with pandas DataFrames on purpose).
- [x] Acceptance: `grep -rn "pl.from_pandas\|import pandas" trendradar/` returns nothing; `pytest` green (esp. `tests/infrastructure/`).
- [x] Note: pandas stays in the environment via tushare's own dependency; the goal is only that OUR code no longer imports it.

## Phase 6: Final verification

- [x] Full suite: `python3 -m pytest -q -p no:cacheprovider` → 258 passed (or more if new tests added).
- [x] `git status --porcelain` shows exactly the intended changes (README.md, selectors/__init__.py, engine.py, tushare/*.py, requirements.txt, this plan file); nothing stray.
- [x] Grep sanity: no `perfect_b1_volume_stepdown` in live code; one `MarketDataStore` class definition; no pandas imports in `trendradar/`.

---

## Sequencing & parallelism

- **Independent (parallel-safe after decisions):** Phase 1 (README), Phase 2 (selectors), Phase 3 (engine), Phase 5 (tushare). Different files, no shared imports.
- **Sequential:** Phase 4 is ops on the user's machine — do it last, after the user stops the service; Phase 6 after everything.
- **Effort:** Phases 1/2/3/5 = S; Phase 4 = M; total small.
