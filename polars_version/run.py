#!/usr/bin/env python3
"""
一键选股程序 (Polars 版)

用法:
  python run.py                     # 全量拉取数据 + 选最新交易日
  python run.py 202503              # 计算 2025年3月 所有交易日选股结果
  python run.py 202501-202503       # 计算 2025年1月~3月 所有交易日
  python run.py --skip-fetch        # 跳过数据拉取，直接选最新交易日
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
POLARS_RESULTS_DIR = str(PROJECT_ROOT / "results" / "polars")
DEFAULT_TUSHARE_TOKEN = "8a835a0cbcf32855a41cfe05457833bfd081de082a2699db11a2c484"


def _resolve_today_arg(value: str) -> str:
    """统一处理 'today' 参数，避免散落在各处的重复判断。"""
    return datetime.today().strftime("%Y%m%d") if str(value).lower() == "today" else value


def _parse_month_range(s: str) -> List[date]:
    parts = s.split("-")
    if len(parts) == 1:
        start_ym = end_ym = parts[0].strip()
    elif len(parts) == 2:
        start_ym, end_ym = parts[0].strip(), parts[1].strip()
    else:
        raise ValueError(f"无法解析: {s}")

    start_dt = datetime.strptime(start_ym, "%Y%m")
    end_dt = datetime.strptime(end_ym, "%Y%m")

    months = []
    cur = start_dt
    while cur <= end_dt:
        months.append(cur.date())
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)
    return months


def _fetch_full(data_dir: str, console, args, start: str, end: str):
    """全量拉取数据并覆盖 db/"""
    from fetch_kline import fetch_one, load_codes_from_stocklist
    import fetch_kline
    import tushare as ts
    import os
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

    os.environ["NO_PROXY"] = "api.waditu.com,.waditu.com,waditu.com"
    os.environ["no_proxy"] = os.environ["NO_PROXY"]
    ts_token = os.environ.get("TUSHARE_TOKEN")
    if not ts_token:
        ts_token = DEFAULT_TUSHARE_TOKEN
    ts.set_token(ts_token)
    fetch_kline.pro = ts.pro_api()

    exclude_boards = set(getattr(args, "exclude_boards", None) or [])
    stocklist = getattr(args, "stocklist", PROJECT_ROOT / "stocklist.csv")
    codes = load_codes_from_stocklist(stocklist, exclude_boards)

    out_dir = Path(data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"[bold]📥 全量拉取数据 {start} → {end} ({len(codes)} 只)...[/bold]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=40),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]下载中...", total=len(codes))
        for code in codes:
            fetch_kline.fetch_one(code, start, end, out_dir)
            progress.advance(task)

    console.print(f"[green]✅ 数据拉取完成，保存至 {out_dir.resolve()}[/green]")
    console.print()


def _run_selection_for_dates(
    dates: List[date],
    data_dir: str,
    config_path: str,
    output_dir: str,
    console,
    strategies_filter: Optional[List[str]] = None,
):
    from select_stock import (
        load_data_polars_table, load_strategies_from_config,
        build_strategy_runner, save_results, print_strategy_result,
        table_to_data_dict,
    )

    console.print(f"[bold]📊 加载数据...[/bold]")
    data_table = load_data_polars_table(data_dir)
    stock_count = data_table["code"].n_unique() if not data_table.is_empty() else 0
    console.print(f"[green]✅ 加载 {stock_count} 只股票数据[/green]")
    console.print()

    strategies = load_strategies_from_config(Path(config_path))
    if strategies_filter:
        strategies = {k: v for k, v in strategies.items() if k in strategies_filter}

    if not strategies:
        console.print("[red]❌ 没有可运行的策略[/red]")
        sys.exit(1)

    total_start = time.time()
    total_dates = len(dates)

    for idx, date_obj in enumerate(dates, 1):
        date_str = date_obj.strftime("%Y-%m-%d")
        console.rule(f"[bold cyan]📅 {date_str}  ({idx}/{total_dates})[/bold cyan]")
        console.print()

        data_dict_cache = None
        def get_data_dict():
            nonlocal data_dict_cache
            if data_dict_cache is None:
                data_dict_cache = table_to_data_dict(data_table)
            return data_dict_cache

        all_results = {}
        day_start = time.time()

        for strategy_name, config in strategies.items():
            emoji = config["emoji"]
            selector = config["selector"]
            runner = build_strategy_runner(selector)

            picks = runner.run_selection(
                date_obj=date_obj,
                data_table=data_table,
                get_data_dict=get_data_dict,
            )

            print_strategy_result(strategy_name, emoji, picks, 0, date_str)

            all_results[strategy_name] = {
                "date": date_str,
                "stocks": picks,
                "count": len(picks),
            }

        day_elapsed = time.time() - day_start
        save_results(all_results, date_str, output_dir)
        console.print(f"[dim]  💾 已保存 | 耗时 {day_elapsed:.1f}s[/dim]")
        console.print()

    total_elapsed = time.time() - total_start
    return total_elapsed


def _get_latest_trade_date(data_dir: str) -> Optional[date]:
    import polars as pl
    db_dir = Path(data_dir)
    try:
        max_date_df = pl.scan_parquet(str(db_dir / "*.parquet")).select(
            pl.col("date").max().alias("max_date")
        ).collect()
        return max_date_df["max_date"][0]
    except Exception:
        return None


def _load_all_trade_dates(data_dir: str) -> List[date]:
    """读取 db 目录中所有可用交易日并排序。"""
    import polars as pl

    return pl.scan_parquet(str(Path(data_dir) / "*.parquet")).select(
        pl.col("date").unique().sort()
    ).collect()["date"].to_list()


def _collect_month_trade_dates(months: List[date], all_trade_dates: List[date]) -> List[date]:
    """根据月份范围筛选交易日。"""
    selected_dates: List[date] = []
    for month_start in months:
        if month_start.month == 12:
            month_end = date(month_start.year, 12, 31)
        else:
            month_end = date(month_start.year, month_start.month + 1, 1) - timedelta(days=1)
        selected_dates.extend(d for d in all_trade_dates if month_start <= d <= month_end)
    selected_dates.sort()
    return selected_dates


def main():
    from rich.console import Console
    from rich.panel import Panel
    from rich import box

    console = Console()

    parser = argparse.ArgumentParser(
        description="一键选股 (Polars 版): 全量拉取数据 + 选股",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run.py                     全量拉取数据，选最新交易日
  python run.py 202503              计算 2025年3月 所有交易日
  python run.py 202501-202503       计算 2025年1月~3月 所有交易日
  python run.py --skip-fetch        跳过数据拉取，直接选最新交易日
  python run.py --strategies B1战法  只运行指定策略
        """,
    )
    parser.add_argument("month", nargs="?", default=None,
                        help="指定月份 YYYYMM 或范围 YYYYMM-YYYYMM")
    parser.add_argument("--start", default="20190101", help="数据拉取起始日期 YYYYMMDD")
    parser.add_argument("--end", default="today", help="数据拉取结束日期 YYYYMMDD")
    parser.add_argument("--stocklist", type=Path, default=PROJECT_ROOT / "stocklist.csv", help="股票清单CSV路径")
    parser.add_argument("--exclude-boards", nargs="*", default=[], choices=["gem", "star", "bj"],
                        help="排除板块：gem(创业板) star(科创板) bj(北交所)")
    parser.add_argument("--data-dir", default=str(PROJECT_ROOT / "db"), help="Parquet 数据目录")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "configs.json"), help="选股配置文件")
    parser.add_argument("--skip-fetch", action="store_true", help="跳过数据拉取")
    parser.add_argument("--strategies", nargs="+", help="指定策略名称")
    args = parser.parse_args()
    fetch_start = _resolve_today_arg(args.start)
    fetch_end = _resolve_today_arg(args.end)

    console.print()
    console.print(
        Panel(
            "[bold cyan]🚀 一键选股系统 (Polars 版)[/bold cyan]",
            border_style="bright_blue",
            box=box.ROUNDED,
        )
    )
    console.print()

    # Step 1: 数据拉取
    if not args.skip_fetch:
        _fetch_full(args.data_dir, console, args, fetch_start, fetch_end)
    else:
        console.print("[dim]⏭️  跳过数据拉取[/dim]")
        console.print()

    # Step 2: 确定选股日期
    if args.month:
        months = _parse_month_range(args.month)
        all_trade_dates = _collect_month_trade_dates(months, _load_all_trade_dates(args.data_dir))

        if not all_trade_dates:
            console.print(f"[red]❌ 指定月份范围内无交易数据: {args.month}[/red]")
            sys.exit(1)

        all_trade_dates.sort()
        console.print(
            f"[bold]📊 共 {len(all_trade_dates)} 个交易日"
            f" ({all_trade_dates[0]} ~ {all_trade_dates[-1]})[/bold]"
        )
        console.print()

        total_elapsed = _run_selection_for_dates(
            all_trade_dates, args.data_dir, args.config, POLARS_RESULTS_DIR, console, args.strategies
        )

        console.print(
            Panel(
                f"[bold green]✅ 全部完成[/bold green]\n"
                f"[bold]交易日数: {len(all_trade_dates)}[/bold]\n"
                f"[bold]总耗时: {total_elapsed:.1f}s[/bold]\n"
                f"[bold]平均: {total_elapsed/len(all_trade_dates):.1f}s/天[/bold]",
                border_style="green",
                box=box.ROUNDED,
            )
        )
    else:
        latest = _get_latest_trade_date(args.data_dir)
        if latest is None:
            console.print("[red]❌ 无数据，请先拉取数据[/red]")
            sys.exit(1)

        console.print(f"[bold]📅 最新交易日: {latest}[/bold]")
        console.print()

        total_elapsed = _run_selection_for_dates(
            [latest], args.data_dir, args.config, POLARS_RESULTS_DIR, console, args.strategies
        )

        console.print(
            Panel(
                f"[bold green]✅ 选股完成[/bold green]\n"
                f"[bold]总耗时: {total_elapsed:.2f} 秒[/bold]",
                border_style="green",
                box=box.ROUNDED,
            )
        )


if __name__ == "__main__":
    main()
