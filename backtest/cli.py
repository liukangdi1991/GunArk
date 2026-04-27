from __future__ import annotations

import argparse
from datetime import datetime
from typing import List

from rich.console import Console
from rich.panel import Panel

from backtest.reports.console import print_strategy_report, print_summary
from backtest.service import build_config, normalize_capital_mode, run_backtest


console = Console()


def _parse_date(s: str):
    return datetime.strptime(s, "%Y%m%d").date()


def _parse_strategy_arg(raw: str) -> List[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


def _normalize_mode(raw: str) -> str:
    return normalize_capital_mode(raw)


def _fail(message: str, code: int = 2) -> None:
    console.print(Panel(f"[bold red]❌ {message}[/bold red]", border_style="red"))
    raise SystemExit(code)


def main() -> None:
    parser = argparse.ArgumentParser(description="A股日线回测系统（基于选股结果JSON）")
    parser.add_argument("--from", dest="start", required=True, help="开始日期 YYYYMMDD")
    parser.add_argument("--to", dest="end", required=True, help="结束日期 YYYYMMDD")
    parser.add_argument("--strategy", help="指定策略名，多个用逗号分隔，如: B1战法,暴力K战法")
    parser.add_argument("--mode", default="unlimited_cash", help="资金模式: realistic / unlimited_cash")
    parser.add_argument("--cash-per-trade", type=float, default=50_000.0, help="unlimited_cash 模式下每票买入金额")
    parser.add_argument("--run-name", help="可选：自定义本次回测结果目录后缀")
    parser.add_argument("--all-strategies", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    start = _parse_date(args.start)
    end = _parse_date(args.end)
    if start > end:
        _fail("--from 不能晚于 --to")
    if args.cash_per_trade <= 0:
        _fail("--cash-per-trade 必须大于0")
    mode = _normalize_mode(args.mode)
    if mode not in {"realistic", "unlimited_cash"}:
        _fail("--mode 仅支持 realistic 或 unlimited_cash")

    cfg = build_config(mode=mode, cash_per_trade=float(args.cash_per_trade))
    if args.strategy:
        strategy_list = _parse_strategy_arg(args.strategy)
        if not strategy_list:
            _fail("--strategy 参数为空，请按 --strategy 策略A,策略B 传入")
    else:
        strategy_list = None

    console.print(
        Panel(
            f"[bold]回测区间:[/bold] {start} ~ {end}\n"
            f"[bold]信号目录:[/bold] {cfg.paths.signal_dir}\n"
            f"[bold]结果目录:[/bold] {cfg.paths.output_root}\n"
            f"[bold]元数据库:[/bold] {cfg.paths.storage_root}/app.db\n"
            f"[bold]资金模式:[/bold] {cfg.capital.mode}\n"
            f"[bold]每票金额:[/bold] {cfg.capital.fixed_cash_per_trade:.0f} (unlimited_cash 生效)\n"
            f"[bold]默认成本:[/bold] 佣金万3 / 印花税卖出万1 / 双边滑点2bp",
            title="🚀 A股日线回测",
            border_style="green",
        )
    )

    def _on_strategy_start(strategy_name: str) -> None:
        console.print(f"\n[bold cyan]▶ 开始回测: {strategy_name}[/bold cyan]")

    def _on_strategy_result(strategy_name: str, result) -> None:
        summary = result["summary"]
        print_strategy_report(
            console=console,
            strategy_name=strategy_name,
            trades=result["trades"],
            skips=result["skips"],
            summary=summary,
        )
        print_summary(console, summary)

    try:
        result = run_backtest(
            start=start,
            end=end,
            strategies=strategy_list,
            mode=cfg.capital.mode,
            cash_per_trade=cfg.capital.fixed_cash_per_trade,
            run_name=args.run_name,
            on_strategy_start=_on_strategy_start,
            on_strategy_result=_on_strategy_result,
        )
    except ValueError as exc:
        _fail(str(exc))

    console.print(f"\n[green]✅ 本次回测输出已保存:[/green] {result['run_dir']}")
    console.print(f"[green]✅ run_id:[/green] {result['run_id']} | [green]策略:[/green] {', '.join(result['strategies'])}")


if __name__ == "__main__":
    main()
