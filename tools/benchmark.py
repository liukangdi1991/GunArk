"""单版本（Polars）选股性能基准测试。"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from run import _get_latest_trade_date  # noqa: E402
from select_stock import (  # noqa: E402
    build_strategy_runner,
    load_data_table,
    load_strategies_from_config,
    table_to_data_dict,
)


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Polars 选股基准测试")
    parser.add_argument("--date", help="选股日期 YYYY-MM-DD，默认取 db 中最新交易日")
    parser.add_argument("--data-dir", default=str(ROOT / "db"))
    parser.add_argument("--config", default=str(ROOT / "configs.json"))
    args = parser.parse_args()

    if args.date:
        trade_date = _parse_date(args.date)
    else:
        latest = _get_latest_trade_date(args.data_dir)
        if latest is None:
            raise SystemExit("db 中无有效数据，无法基准测试")
        trade_date = latest

    print(f"基准日期: {trade_date}")
    print("加载数据...")
    t0 = time.perf_counter()
    data_table = load_data_table(args.data_dir)
    load_elapsed = time.perf_counter() - t0
    if data_table.is_empty():
        raise SystemExit("未加载到行情数据")
    print(f"数据加载耗时: {load_elapsed:.3f}s, 股票数: {data_table['code'].n_unique()}")

    strategies = load_strategies_from_config(Path(args.config))
    if not strategies:
        raise SystemExit("未找到可用策略")

    print("\n逐策略耗时:")
    print(f"{'策略':<20} {'数量':>8} {'耗时(s)':>10}")
    print("-" * 42)

    total = 0.0
    data_dict_cache = None

    def get_data_dict():
        nonlocal data_dict_cache
        if data_dict_cache is None:
            data_dict_cache = table_to_data_dict(data_table)
        return data_dict_cache

    for strategy_name, strategy_cfg in strategies.items():
        runner = build_strategy_runner(strategy_cfg["selector"])
        t1 = time.perf_counter()
        picks = runner.run_selection(
            date_obj=trade_date,
            data_table=data_table,
            get_data_dict=get_data_dict,
        )
        elapsed = time.perf_counter() - t1
        total += elapsed
        print(f"{strategy_name:<20} {len(picks):>8} {elapsed:>10.3f}")

    print("-" * 42)
    print(f"{'策略总耗时':<20} {'':>8} {total:>10.3f}")
    print(f"{'总耗时(含加载)':<20} {'':>8} {load_elapsed + total:>10.3f}")


if __name__ == "__main__":
    main()
