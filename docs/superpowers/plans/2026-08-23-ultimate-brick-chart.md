# 极致砖型图选股 策略 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans.

**Goal:** Add 「极致砖型图选股」: brick MT turn-up (C1/C2, same as 砖型图) + strong-trend filter (C3 ZXK>DKK, C4 close>=ZXK), reusing `compute_mt`/`compute_zx_lines`.

**Architecture:** Same shape as `OversoldBottomFishingSelector` minus MACD, with reversed trend filter; per-code ewm (MT/ZXK), flat DKK, null/NaN guards, len≥115.

**Spec:** `docs/superpowers/specs/2026-08-23-ultimate-brick-chart-design.md`

**Baseline:** 302 passed.

---

### Task 1: Reference + decision tests (TDD)

**Files:** Create `tests/domain/test_selectors/test_ultimate_brick_chart.py`

- [ ] Reference test: MT + ZXK series vs Python reference (`_sma_ref`/`_ewma_ref` — same helpers as oversold test).
- [ ] Decision tests (crafted 120-row hist with mt/zxk/dkk/close columns):
  - `test_all_conditions_selected`: mt5=[8,7,6,5,8] (C1+C2 ✅), zxk=90 < dkk=100, close=95 (C3: 90>100 ❌...) — use zxk=110, dkk=90, close=115 → C3 110>90 ✅, C4 115>=110 ✅.
  - `test_c1_fails_when_mt_not_rising`: mt5=[8,7,6,5,4]
  - `test_c2_fails_when_green_chain_broken`: mt5=[8,7,9,5,8]
  - `test_c3_fails_when_zxk_below_dkk`: zxk=90, dkk=100
  - `test_c4_fails_when_close_below_zxk`: close=85, zxk=110, dkk=90
  - `test_nan_mt_excluded`, `test_short_history_excluded`

### Task 2: Implement selector + register

- Create `selectors/ultimate_brick_chart.py` (mirror oversold structure: per-code compute_mt + zxk, flat dkk, guards).
- Register `ultimate_brick_chart` (name 极致砖型图选股); `test_api_contract.py` 12→13.

### Task 3: Full suite, deploy, live run

- `python3 -m pytest -q -p no:cacheprovider` → 310+ passed; commit.
- `./scripts/restart.sh`; run live selection; user compares with TDX.
