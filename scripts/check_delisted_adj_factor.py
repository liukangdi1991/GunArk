#!/usr/bin/env python
"""退市股 adj_factor 可得性实测 —— L∪D 上线前的前置检查（只读）。

为什么要跑
----------
adj_factor 取不到现在是硬失败（`fetch.AdjFactorUnavailable`），逐股路径判 `code`
类：计次 → 3 轮后出列。spec §1 只实测过退市股 `daily` 可拉（000004.SZ 2,561 行），
**没实测过退市股的 adj_factor**。若 Tushare 对退市股系统性不给因子，那么「把 339
只退市股并进来修正幸存者偏差」这件事本身会变成一个永久缺口，而且分两种坏法：

    缺口只数 > 5% × 有效清单  ⇒ 每轮都撞整批熔断（§3.7 主保险）
                               ⇒ 全量基线永远建不起来；
    缺口只数 ≤ 5%             ⇒ 健康轮 ⇒ 3 轮后出列 ⇒ 带缺口提交
                               ⇒ 永久少这批股（幸存者偏差换个形式回来）。

5% 是熔断阈值，所以真正要看的不是「有没有退市股缺因子」，而是**缺的只数有没有
越过 5% × 有效清单**（约 5.5k 只时阈值约 278 只）。本脚本直接把这个数算出来。

跑法
----
需要真 token；只读，不写 stock_meta.parquet、不写 bars、不动账本：

    TUSHARE_TOKEN=xxx .venv/bin/python scripts/check_delisted_adj_factor.py

    --limit N       只测前 N 只退市股（先抽样看苗头）
    --control N     在市股对照组大小（默认 10，用来确认接口本身是通的）
    --sleep S       每只之间的间隔秒数（默认 0.15）

判读
----
- 对照组全 ok、退市组出现 adj_empty ⇒ 边界真实存在，按上面的分支决定兜底口径；
- 对照组也 adj_empty ⇒ 是接口/权限/额度问题，不是退市股边界，先修环境再重跑；
- 退市组全 ok ⇒ 风险不存在，本脚本可以删。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl  # noqa: E402

from trendradar.domain.market.sync.spec import (  # noqa: E402
    BASELINE_START,
    SHANGHAI,
    latest_tradeable_day,
)
from trendradar.infrastructure.tushare.calendar import fetch_trade_calendar  # noqa: E402
from trendradar.infrastructure.tushare.client import get_pro  # noqa: E402
from trendradar.infrastructure.tushare.fetch import fetch_code_range  # noqa: E402
from trendradar.infrastructure.tushare.stocklist import (  # noqa: E402
    _FIELDS,
    build_effective_list,
    normalize_stock_meta,
)

FULL_FAIL_RATE_BREAKER = 0.05


def _load_meta(pro) -> pl.DataFrame:
    """L∪D 两次 stock_basic，走生产同一条 normalize，但**不落盘**。"""
    frames = [
        pro.stock_basic(exchange="", list_status=s, fields=_FIELDS)
        for s in ("L", "D")
    ]
    meta = normalize_stock_meta(frames)
    if meta.is_empty():
        raise SystemExit("stock_basic L/D 都返回空，检查 token 权限")
    return meta


def _latest_tradeable(pro, today: date) -> date:
    cal = fetch_trade_calendar(pro, BASELINE_START, today)
    latest = latest_tradeable_day(set(cal), datetime.now(SHANGHAI))
    if latest is None:
        raise SystemExit("trade_cal 返回空")
    return latest


def _probe(pro, code: str, start: date, end: date, sleep: float) -> tuple[str, str]:
    """跑一次生产路径的 fetch_code_range，把结果归成四类之一。"""
    time.sleep(sleep)
    r = fetch_code_range(pro, code, start, end)
    if r.kind is None:
        # 成功：因子覆盖完整（不完整会抛 AdjFactorUnavailable，走不到这里）
        return "ok", f"{r.df.height} 行"
    err = r.error or ""
    if "adj_factor" in err:
        return "adj_empty", err
    if r.kind.value == "ok_empty":
        return "daily_empty", "daily 区间内无行"
    return r.kind.value, err


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=0, help="只测前 N 只退市股（0=全测）")
    ap.add_argument("--control", type=int, default=10, help="在市股对照组大小")
    ap.add_argument("--sleep", type=float, default=0.15, help="每只间隔秒数")
    ap.add_argument("--exclude-boards", default="",
                    help="逗号分隔，与面板 exclude_boards 一致（默认空，同 service.py:107）")
    args = ap.parse_args()

    if not os.environ.get("TUSHARE_TOKEN", "").strip():
        # 不拦的话 get_pro() 照样返回 client，第一次 stock_basic 才炸一屏鉴权栈
        print("缺少 TUSHARE_TOKEN：TUSHARE_TOKEN=xxx "
              ".venv/bin/python scripts/check_delisted_adj_factor.py", file=sys.stderr)
        return 2

    pro = get_pro()
    today = datetime.now(SHANGHAI).date()
    meta = _load_meta(pro)
    latest = _latest_tradeable(pro, today)
    boards = [b.strip() for b in args.exclude_boards.split(",") if b.strip()]
    effective = build_effective_list(meta, boards, latest)
    print(f"最新可得交易日 {latest}；有效清单 {len(effective.codes)} 只"
          f"（已剔北交所/排除板块/未来上市/退市早于 {BASELINE_START}）")

    delist_map = dict(zip(effective.codes,
                          (r[1] for r in effective.rows), strict=True))
    # normalize_stock_meta 结尾的 polars unique() 不保序，不排序的话 --limit
    # 每次抽到的是不同的一批，外推值不可复现
    delisted = sorted(c for c in effective.codes if delist_map[c] is not None)
    listed = sorted(c for c in effective.codes if delist_map[c] is None)
    print(f"其中退市股 {len(delisted)} 只、在市股 {len(listed)} 只")

    breaker_at = int(FULL_FAIL_RATE_BREAKER * len(effective.codes))
    print(f"熔断阈值：失败 > {breaker_at} 只（{FULL_FAIL_RATE_BREAKER:.0%} × "
          f"{len(effective.codes)}）即整批熔断，全量基线建不起来\n")

    targets = delisted[: args.limit] if args.limit else delisted
    control = listed[: args.control]

    print(f"== 对照组：在市股 {len(control)} 只 ==")
    ctrl_bad = 0
    for code in control:
        start, end = effective.clamped_range(code)
        verdict, detail = _probe(pro, code, start, end, args.sleep)
        if verdict != "ok":
            ctrl_bad += 1
            print(f"  {code} [{start}..{end}] {verdict}: {detail}")
    print(f"  对照组异常 {ctrl_bad}/{len(control)}"
          + ("（接口本身有问题，退市组结论不可信，先修环境）" if ctrl_bad else " ✓\n"))
    if ctrl_bad:
        return 2

    print(f"== 退市组：{len(targets)} 只 ==")
    buckets: dict[str, list[str]] = {}
    for i, code in enumerate(targets, 1):
        start, end = effective.clamped_range(code)
        verdict, detail = _probe(pro, code, start, end, args.sleep)
        buckets.setdefault(verdict, []).append(code)
        if verdict != "ok":
            print(f"  {code} 退市日 {delist_map[code]} [{start}..{end}] "
                  f"{verdict}: {detail}")
        if i % 50 == 0:
            print(f"  ...{i}/{len(targets)}")

    print("\n== 汇总 ==")
    for verdict in sorted(buckets):
        print(f"  {verdict:12s} {len(buckets[verdict]):4d} 只")

    adj_empty = len(buckets.get("adj_empty", []))
    if not adj_empty:
        print("\n结论：退市股 adj_factor 可得，硬失败不会造成缺口。风险不存在。")
        return 0

    print(f"\n结论：{adj_empty} 只退市股取不到 adj_factor。")
    if args.limit and args.limit < len(delisted):
        projected = round(adj_empty / len(targets) * len(delisted))
        print(f"  这是抽样（{len(targets)}/{len(delisted)}），按同比例外推全量约 "
              f"{projected} 只 —— 请去掉 --limit 重跑取准数。")
        adj_empty = projected
    if adj_empty > breaker_at:
        print(f"  {adj_empty} > {breaker_at} ⇒ **每轮必撞整批熔断，全量基线永远建不起来**。")
        print("  必须改设计，不能直接上线。可选口径见 spec §3.7 的退市股因子边界一节。")
        return 1
    print(f"  {adj_empty} ≤ {breaker_at} ⇒ 健康轮 ⇒ 3 轮后出列 ⇒ 带缺口提交。")
    print(f"  基线能建起来，但会永久少这 {adj_empty} 只退市股 —— 幸存者偏差换个形式回来。")
    print("  需在 spec 记录该边界并决定兜底口径。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
