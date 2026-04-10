#!/usr/bin/env python3
"""
一键选股程序：自动拉取数据 + 运行选股
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import logging
import os
import sys
import time
import warnings
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pandas as pd
import tushare as ts
from tqdm import tqdm
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich import box

warnings.filterwarnings("ignore")

# ==================== Rich 控制台 ====================
console = Console()

# ==================== 日志配置 ====================
LOG_FILE = Path("run.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger("stock_selector")


# ==================== 数据拉取模块 ====================

COOLDOWN_SECS = 600
BAN_PATTERNS = (
    "访问频繁", "请稍后", "超过频率", "频繁访问",
    "too many requests", "429",
    "forbidden", "403",
    "max retries exceeded"
)

pro: ts.pro_api = None


def _looks_like_ip_ban(exc: Exception) -> bool:
    msg = (str(exc) or "").lower()
    return any(pat in msg for pat in BAN_PATTERNS)


def _cool_sleep(base_seconds: int) -> None:
    import random
    jitter = random.uniform(0.9, 1.2)
    sleep_s = max(1, int(base_seconds * jitter))
    console.print(f"[yellow]⚠️  疑似被限流/封禁，进入冷却期 {sleep_s} 秒...[/yellow]")
    time.sleep(sleep_s)


def _to_ts_code(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("60", "68", "9")):
        return f"{code}.SH"
    elif code.startswith(("4", "8")):
        return f"{code}.BJ"
    else:
        return f"{code}.SZ"


def _get_kline_tushare(code: str, start: str, end: str) -> pd.DataFrame:
    ts_code = _to_ts_code(code)
    try:
        df = ts.pro_bar(
            ts_code=ts_code,
            adj="qfq",
            start_date=start,
            end_date=end,
            freq="D",
            api=pro
        )
    except Exception as e:
        if _looks_like_ip_ban(e):
            raise RuntimeError(str(e)) from e
        raise

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.rename(columns={"trade_date": "date", "vol": "volume"})[
        ["date", "open", "close", "high", "low", "volume"]
    ].copy()
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "close", "high", "low", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("date").reset_index(drop=True)


def validate(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    if df["date"].isna().any():
        raise ValueError("存在缺失日期！")
    if (df["date"] > pd.Timestamp.today()).any():
        raise ValueError("数据包含未来日期，可能抓取错误！")
    return df


def _filter_by_boards_stocklist(df: pd.DataFrame, exclude_boards: set[str]) -> pd.DataFrame:
    code = df["symbol"].astype(str)
    ts_code = df["ts_code"].astype(str).str.upper()
    mask = pd.Series(True, index=df.index)

    if "gem" in exclude_boards:
        mask &= ~code.str.startswith(("300", "301"))
    if "star" in exclude_boards:
        mask &= ~code.str.startswith(("688",))
    if "bj" in exclude_boards:
        mask &= ~(ts_code.str.endswith(".BJ") | code.str.startswith(("4", "8")))

    return df[mask].copy()


def load_codes_from_stocklist(stocklist_csv: Path, exclude_boards: set[str]) -> List[str]:
    df = pd.read_csv(stocklist_csv)
    df = _filter_by_boards_stocklist(df, exclude_boards)
    codes = df["symbol"].astype(str).str.zfill(6).tolist()
    codes = list(dict.fromkeys(codes))
    console.print(f"[cyan]📋 从 {stocklist_csv} 读取到 {len(codes)} 只股票[/cyan]")
    if exclude_boards:
        console.print(f"[dim]   排除板块: {', '.join(sorted(exclude_boards))}[/dim]")
    return codes


def fetch_one(code: str, start: str, end: str, out_dir: Path):
    csv_path = out_dir / f"{code}.csv"

    for attempt in range(1, 4):
        try:
            new_df = _get_kline_tushare(code, start, end)
            if new_df.empty:
                new_df = pd.DataFrame(columns=["date", "open", "close", "high", "low", "volume"])
            new_df = validate(new_df)
            new_df.to_csv(csv_path, index=False)
            break
        except Exception as e:
            if _looks_like_ip_ban(e):
                console.print(f"[red]❌ {code} 第 {attempt} 次抓取疑似被封禁，沉睡 {COOLDOWN_SECS} 秒[/red]")
                _cool_sleep(COOLDOWN_SECS)
            else:
                silent_seconds = 15 * attempt
                console.print(f"[yellow]⏳ {code} 第 {attempt} 次抓取失败，{silent_seconds} 秒后重试[/yellow]")
                time.sleep(silent_seconds)
    else:
        console.print(f"[red]❌ {code} 三次抓取均失败，已跳过！[/red]")


def fetch_data(start: str, end: str, stocklist: Path, exclude_boards: set, out_dir: Path):
    """拉取数据"""
    global pro
    
    # 初始化 Tushare
    os.environ["NO_PROXY"] = "api.waditu.com,.waditu.com,waditu.com"
    os.environ["no_proxy"] = os.environ["NO_PROXY"]
    ts_token = os.environ.get("TUSHARE_TOKEN", "8a835a0cbcf32855a41cfe05457833bfd081de082a2699db11a2c484")
    ts.set_token(ts_token)
    pro = ts.pro_api()

    out_dir.mkdir(parents=True, exist_ok=True)

    codes = load_codes_from_stocklist(stocklist, exclude_boards)
    if not codes:
        console.print("[red]❌ stocklist 为空或被过滤后无代码，请检查。[/red]")
        return False

    # 显示下载信息面板
    info_panel = Panel(
        f"[bold cyan]📥 开始拉取数据[/bold cyan]\n\n"
        f"  股票数量: [bold green]{len(codes)}[/bold green]\n"
        f"  日期范围: [bold]{start}[/bold] → [bold]{end}[/bold]\n"
        f"  并发度: [bold yellow]1 (单线程)[/bold yellow]\n"
        f"  保存目录: [dim]{out_dir.resolve()}[/dim]",
        title="[bold blue]数据拉取[/bold blue]",
        border_style="blue",
        box=box.ROUNDED
    )
    console.print(info_panel)
    console.print()

    # 使用单线程顺序下载
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=40),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]下载中...", total=len(codes))
        for code in codes:
            fetch_one(code, start, end, out_dir)
            progress.advance(task)

    console.print()
    console.print(f"[green]✅ 数据拉取完成，已保存至 {out_dir.resolve()}[/green]")
    return True


# ==================== 选股模块 ====================

def load_data(data_dir: Path, codes: Iterable[str]) -> Dict[str, pd.DataFrame]:
    frames: Dict[str, pd.DataFrame] = {}
    for code in codes:
        fp = data_dir / f"{code}.csv"
        if not fp.exists():
            continue
        df = pd.read_csv(fp, parse_dates=["date"]).sort_values("date")
        frames[code] = df
    return frames


def load_config(cfg_path: Path) -> List[Dict[str, Any]]:
    if not cfg_path.exists():
        console.print(f"[red]❌ 配置文件 {cfg_path} 不存在[/red]")
        sys.exit(1)
    with cfg_path.open(encoding="utf-8") as f:
        cfg_raw = json.load(f)

    if isinstance(cfg_raw, list):
        cfgs = cfg_raw
    elif isinstance(cfg_raw, dict) and "selectors" in cfg_raw:
        cfgs = cfg_raw["selectors"]
    else:
        cfgs = [cfg_raw]

    if not cfgs:
        console.print("[red]❌ configs.json 未定义任何 Selector[/red]")
        sys.exit(1)

    return cfgs


def instantiate_selector(cfg: Dict[str, Any]):
    cls_name: str = cfg.get("class")
    if not cls_name:
        raise ValueError("缺少 class 字段")

    try:
        module = importlib.import_module("Selector")
        cls = getattr(module, cls_name)
    except (ModuleNotFoundError, AttributeError) as e:
        raise ImportError(f"无法加载 Selector.{cls_name}: {e}") from e

    params = cfg.get("params", {})
    return cfg.get("alias", cls_name), cls(**params)


def print_banner():
    """打印程序横幅"""
    banner = """
    ╔══════════════════════════════════════════════════════════════════╗
    ║                                                                  ║
    ║     ███████╗████████╗ ██████╗  ██████╗██╗  ██╗                  ║
    ║     ██╔════╝╚══██╔══╝██╔═══██╗██╔════╝██║ ██╔╝                  ║
    ║     ███████╗   ██║   ██║   ██║██║     █████╔╝                   ║
    ║     ╚════██║   ██║   ██║   ██║██║     ██╔═██╗                   ║
    ║     ███████║   ██║   ╚██████╔╝╚██████╗██║  ██╗                  ║
    ║     ╚══════╝   ╚═╝    ╚═════╝  ╚═════╝╚═╝  ╚═╝                  ║
    ║                                                                  ║
    ║              📈 智能选股系统 v2.0 📈                              ║
    ║           Stock Intelligent Selection System                     ║
    ║                                                                  ║
    ╚══════════════════════════════════════════════════════════════════╝
    """
    console.print(banner, style="bold cyan")


def print_section(title: str, icon: str = "📌"):
    """打印分节标题"""
    console.print()
    console.rule(f"[bold cyan]{icon} {title}[/bold cyan]", style="cyan")
    console.print()


def print_selector_result(alias: str, trade_date: pd.Timestamp, picks: List[str], stock_names: Dict[str, str]):
    """美化输出选股结果"""
    # 创建表格
    table = Table(
        title=f"🎯 {alias}",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
        border_style="blue",
        title_style="bold cyan",
    )
    
    table.add_column("序号", style="dim", width=6, justify="center")
    table.add_column("股票代码", style="cyan", width=12)
    table.add_column("股票名称", style="green", width=20)
    
    if picks:
        for i, code in enumerate(picks, 1):
            name = stock_names.get(code, "")
            table.add_row(str(i), code, name)
    else:
        table.add_row("-", "无符合条件股票", "-")
    
    # 添加统计信息
    console.print()
    console.print(table)
    
    # 显示统计摘要
    stats_text = Text()
    stats_text.append(f"📅 交易日: ", style="dim")
    stats_text.append(f"{trade_date.strftime('%Y-%m-%d')}", style="bold")
    stats_text.append(f"  |  📊 符合条件: ", style="dim")
    stats_text.append(f"{len(picks)}", style="bold green")
    stats_text.append(f" 只", style="dim")
    console.print(stats_text, justify="center")
    console.print()


def save_results_to_file(results: Dict[str, List[str]], trade_date: pd.Timestamp, 
                         stock_names: Dict[str, str], results_dir: Path):
    """保存选股结果到文件"""
    results_dir.mkdir(parents=True, exist_ok=True)
    
    # 使用选股日期命名文件
    date_str = trade_date.strftime('%Y%m%d')
    result_file = results_dir / f"selection_{date_str}.txt"
    
    with open(result_file, 'w', encoding='utf-8') as f:
        f.write(f"{'='*70}\n")
        f.write(f"  📈 股票选股结果报告\n")
        f.write(f"  📅 选股日期: {trade_date.strftime('%Y-%m-%d')}\n")
        f.write(f"  🕐 生成时间: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"{'='*70}\n\n")
        
        total_picks = 0
        for alias, picks in results.items():
            total_picks += len(picks)
            f.write(f"┌{'─'*68}┐\n")
            f.write(f"│  🎯 {alias:<63}│\n")
            f.write(f"├{'─'*68}┤\n")
            f.write(f"│  📊 符合条件: {len(picks):<55}│\n")
            f.write(f"├{'─'*68}┤\n")
            if picks:
                for i, code in enumerate(picks, 1):
                    name = stock_names.get(code, "")
                    display = f"{code} {name}" if name else code
                    f.write(f"│  {i:>3}. {display:<62}│\n")
            else:
                f.write(f"│  {'❌ 无符合条件股票':<65}│\n")
            f.write(f"└{'─'*68}┘\n\n")
        
        f.write(f"{'='*70}\n")
        f.write(f"  📊 总计选出 {total_picks} 只股票\n")
        f.write(f"{'='*70}\n")
    
    console.print(f"[green]📄 选股结果已保存至: {result_file}[/green]")
    return result_file


def load_stock_names(stocklist_csv: Path) -> Dict[str, str]:
    """加载股票名称"""
    try:
        df = pd.read_csv(stocklist_csv)
        return dict(zip(df['symbol'].astype(str).str.zfill(6), df['name']))
    except Exception:
        return {}


def run_selection(data_dir: Path, config_path: Path, trade_date_str: str = None, 
                  results_dir: Path = Path("./results")):
    """运行选股"""
    print_section("开始选股分析", "🔍")
    
    # 加载数据
    codes = [f.stem for f in data_dir.glob("*.csv")]
    if not codes:
        console.print(f"[red]❌ 数据目录 {data_dir} 中没有CSV文件[/red]")
        return False
    
    with console.status("[bold green]加载数据中...[/bold green]"):
        data = load_data(data_dir, codes)
    
    if not data:
        console.print("[red]❌ 未能加载任何行情数据[/red]")
        return False
    
    console.print(f"[green]✅ 已加载 {len(data)} 只股票的数据[/green]")
    
    # 确定交易日期
    if trade_date_str:
        trade_date = pd.to_datetime(trade_date_str)
    else:
        max_dates = (df["date"].max() for df in data.values() if not df.empty)
        max_dates = [d for d in max_dates if not pd.isna(d)]
        if not max_dates:
            console.print("[red]❌ 所有数据文件中都没有有效的日期数据[/red]")
            return False
        trade_date = max(max_dates)
    
    console.print(f"[cyan]📅 选股日期: {trade_date.strftime('%Y-%m-%d')}[/cyan]")
    
    # 加载选股器配置
    selector_cfgs = load_config(config_path)
    
    # 加载股票名称
    stock_names = load_stock_names(Path("./stocklist.csv"))
    
    # 运行选股
    all_results = {}
    
    for cfg in selector_cfgs:
        if cfg.get("activate", True) is False:
            continue
        try:
            alias, selector = instantiate_selector(cfg)
        except Exception as e:
            console.print(f"[red]❌ 跳过配置 {cfg}: {e}[/red]")
            continue
        
        with console.status(f"[bold cyan]运行 {alias}...[/bold cyan]"):
            picks = selector.select(trade_date, data)
        
        all_results[alias] = picks
        
        # 美化输出结果
        print_selector_result(alias, trade_date, picks, stock_names)
    
    # 保存结果到文件
    if all_results:
        result_file = save_results_to_file(all_results, trade_date, stock_names, results_dir)
        return result_file
    
    return None


# ==================== 主函数 ====================

def main():
    print_banner()
    
    parser = argparse.ArgumentParser(
        description="一键选股程序：自动拉取数据 + 运行选股",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python run.py                          # 使用默认配置运行
  python run.py --date 2024-01-15        # 指定选股日期
  python run.py --skip-fetch             # 跳过数据拉取，直接选股
  python run.py --start 20230101         # 指定数据拉取起始日期
        """
    )
    
    # 数据拉取参数
    parser.add_argument("--start", default="20190101", help="数据拉取起始日期 YYYYMMDD")
    parser.add_argument("--end", default="today", help="数据拉取结束日期 YYYYMMDD")
    parser.add_argument("--stocklist", type=Path, default=Path("./stocklist.csv"), help="股票清单CSV路径")
    parser.add_argument(
        "--exclude-boards",
        nargs="*",
        default=[],
        choices=["gem", "star", "bj"],
        help="排除板块：gem(创业板) star(科创板) bj(北交所)"
    )
    parser.add_argument("--data-dir", default="./data", help="数据保存目录")
    parser.add_argument("--skip-fetch", action="store_true", help="跳过数据拉取")
    
    # 选股参数
    parser.add_argument("--config", default="./configs.json", help="选股器配置文件")
    parser.add_argument("--date", help="选股日期 YYYY-MM-DD（默认使用数据最新日期）")
    parser.add_argument("--results-dir", default="./results", help="结果保存目录")
    
    args = parser.parse_args()
    
    start_time = time.time()
    
    # Step 1: 拉取数据
    if not args.skip_fetch:
        start = dt.date.today().strftime("%Y%m%d") if str(args.start).lower() == "today" else args.start
        end = dt.date.today().strftime("%Y%m%d") if str(args.end).lower() == "today" else args.end
        
        success = fetch_data(
            start=start,
            end=end,
            stocklist=args.stocklist,
            exclude_boards=set(args.exclude_boards),
            out_dir=Path(args.data_dir)
        )
        if not success:
            console.print("[red]❌ 数据拉取失败，程序终止[/red]")
            sys.exit(1)
    else:
        print_section("跳过数据拉取", "⏭️")
    
    # Step 2: 运行选股
    result_file = run_selection(
        data_dir=Path(args.data_dir),
        config_path=Path(args.config),
        trade_date_str=args.date,
        results_dir=Path(args.results_dir)
    )
    
    # 完成
    elapsed = time.time() - start_time
    
    print_section("选股完成", "✅")
    
    # 显示完成面板
    complete_panel = Panel(
        f"[bold green]🎉 选股任务已完成！[/bold green]\n\n"
        f"  ⏱️  总耗时: [bold]{elapsed:.1f}[/bold] 秒\n"
        f"  📄 结果文件: [cyan]{result_file if result_file else '无'}[/cyan]",
        title="[bold green]任务完成[/bold green]",
        border_style="green",
        box=box.DOUBLE_EDGE
    )
    console.print(complete_panel)
    console.print()


if __name__ == "__main__":
    main()