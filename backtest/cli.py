from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import List

from rich.console import Console
from rich.panel import Panel

from backtest import BacktestEngine, default_config
from backtest.reports.console import print_strategy_report, print_summary
from backtest.reports.writer import save_run_outputs
from backtest.strategy_mapper import strategy_english_name


console = Console()


def _parse_date(s: str):
    return datetime.strptime(s, "%Y%m%d").date()


def main() -> None:
    parser = argparse.ArgumentParser(description="A股日线回测系统（基于选股结果JSON）")
    parser.add_argument("--from", dest="start", required=True, help="开始日期 YYYYMMDD")
    parser.add_argument("--to", dest="end", required=True, help="结束日期 YYYYMMDD")
    parser.add_argument("--strategy", help="指定策略名")
    parser.add_argument("--all-strategies", action="store_true", help="回测区间内所有策略")
    args = parser.parse_args()

    start = _parse_date(args.start)
    end = _parse_date(args.end)
    if start > end:
        raise ValueError("--from 不能晚于 --to")

    cfg = default_config()
    engine = BacktestEngine(cfg)

    if args.all_strategies:
        strategy_list: List[str] = engine.list_available_strategies(start, end)
    elif args.strategy:
        strategy_list = [args.strategy]
    else:
        raise ValueError("请使用 --strategy 或 --all-strategies")

    if not strategy_list:
        raise ValueError("未找到可回测策略")

    console.print(
        Panel(
            f"[bold]回测区间:[/bold] {start} ~ {end}\n"
            f"[bold]信号目录:[/bold] {cfg.paths.signal_dir}\n"
            f"[bold]结果目录:[/bold] {cfg.paths.output_root}\n"
            f"[bold]默认成本:[/bold] 佣金万3 / 印花税卖出万1 / 双边滑点2bp",
            title="🚀 A股日线回测",
            border_style="green",
        )
    )

    for strategy_name in strategy_list:
        console.print(f"\n[bold cyan]▶ 开始回测: {strategy_name}[/bold cyan]")
        result = engine.run(start=start, end=end, strategy_name=strategy_name)
        summary = result["summary"]
        strategy_dir_name = strategy_english_name(strategy_name)
        run_dir = save_run_outputs(
            output_root=Path(cfg.paths.output_root),
            strategy_dir_name=strategy_dir_name,
            daily_equity=result["daily_equity"],
            trades=result["trades"],
            skips=result["skips"],
            summary=summary,
        )
        print_strategy_report(
            console=console,
            strategy_name=strategy_name,
            trades=result["trades"],
            skips=result["skips"],
            summary=summary,
        )
        print_summary(console, summary)
        console.print(f"[green]✅ 输出已保存:[/green] {run_dir}")


if __name__ == "__main__":
    main()
