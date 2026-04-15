from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.table import Table


@lru_cache(maxsize=1)
def _load_stock_names() -> dict[str, str]:
    root = Path(__file__).resolve().parent.parent.parent
    stocklist = root / "stocklist.csv"
    if not stocklist.exists():
        return {}
    try:
        df = pd.read_csv(stocklist, usecols=["symbol", "name"])
    except Exception:
        return {}
    return {str(row["symbol"]).zfill(6): str(row["name"]) for _, row in df.iterrows()}


def print_strategy_report(
    console: Console,
    strategy_name: str,
    trades: pd.DataFrame,
    skips: pd.DataFrame,
    summary: dict,
) -> None:
    if trades.empty:
        console.print(f"[yellow]⚠️ {strategy_name}: 无有效成交交易[/yellow]")
    else:
        table = Table(
            title=f"🎯 {strategy_name}",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("股票代码", style="cyan", width=10)
        table.add_column("股票名称", width=10)
        table.add_column("买入日", width=10)
        table.add_column("买入价", justify="right", width=10)
        table.add_column("卖出日", width=10)
        table.add_column("卖出价", justify="right", width=10)
        table.add_column("股数", justify="right", width=8)
        table.add_column("收益金额", justify="right", width=13)
        table.add_column("收益率", justify="right", width=10)
        table.add_column("备注", width=10)

        sorted_trades = trades.sort_values(["sell_date", "code"]).reset_index(drop=True)
        stock_names = _load_stock_names()
        for _, row in sorted_trades.iterrows():
            is_win = float(row["profit"]) > 0
            style = "red" if is_win else "green"
            symbol = "[red]▲[/red]" if is_win else "[green]▼[/green]"
            delay_note = f"延迟{int(row['sell_postpone_days'])}天" if int(row["sell_postpone_days"]) > 0 else ""
            code = str(row["code"]).zfill(6)
            name = stock_names.get(code, "")
            table.add_row(
                code,
                name[:4],
                str(row["buy_date"]),
                f"{float(row['buy_price']):.2f}",
                str(row["sell_date"]),
                f"{float(row['sell_price']):.2f}",
                f"{int(row['shares'])}",
                f"{symbol} [{style}]{float(row['profit']):+,.2f}[/{style}]",
                f"[{style}]{float(row['return_pct']):+,.2f}%[/{style}]",
                delay_note,
            )
        console.print(table)

    total = int(summary.get("trade_count", 0))
    win_rate = float(summary.get("win_rate_pct", 0.0))
    win_cnt = int(round(total * win_rate / 100.0))
    loss_cnt = total - win_cnt
    console.print(
        f"  📊 有效交易: {total} | 盈利: {win_cnt} | 亏损: {loss_cnt} | 胜率: {win_rate:.1f}%"
    )

    if not skips.empty:
        skip_msgs = []
        for _, s in skips.sort_values(["signal_date", "code"]).iterrows():
            date_ref = str(s["date_ref"]) if pd.notna(s["date_ref"]) else "-"
            skip_msgs.append(f"{s['code']}-{s['stage']}-{date_ref}-{s['reason']}")
        console.print(f"  ⚠️ 跳过: {', '.join(skip_msgs)}")


def print_summary(console: Console, summary: dict) -> None:
    table = Table(title=f"📊 {summary['strategy']} 回测摘要", show_header=False)
    table.add_column("k", style="cyan", width=18)
    table.add_column("v", style="bold")
    ret_color = "red" if float(summary["total_return_pct"]) >= 0 else "green"
    annual_color = "red" if float(summary["annual_return_pct"]) >= 0 else "green"
    table.add_row("区间", f"{summary['start_date']} ~ {summary['end_date']}")
    table.add_row("交易笔数", str(summary["trade_count"]))
    table.add_row("胜率", f"{summary['win_rate_pct']:.2f}%")
    table.add_row("总收益率", f"[{ret_color}]{summary['total_return_pct']:.2f}%[/{ret_color}]")
    table.add_row("年化收益率", f"[{annual_color}]{summary['annual_return_pct']:.2f}%[/{annual_color}]")
    table.add_row("最大回撤", f"{summary['max_drawdown_pct']:.2f}%")
    table.add_row("Sharpe", f"{summary['sharpe']:.3f}")
    table.add_row("最终现金", f"{summary['final_cash']:.2f}")
    table.add_row("未平仓数", str(summary["open_positions"]))
    console.print(table)
