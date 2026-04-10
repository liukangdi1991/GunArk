"""
选股程序 - Polars 版本
- 使用 Polars 直接读取 CSV（不转换）
- 保持与原版完全一致的选股逻辑
- 美观的 Rich 输出界面
"""

import argparse
import importlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import polars as pl
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from Selector_polars import (
    BBIKDJSelector,
    SuperB1Selector,
    PeakKDJSelector,
    BBIShortLongSelector,
    MA60CrossVolumeWaveSelector,
    BigBullishVolumeSelector,
)

console = Console()

# ─────────────────────────── 配置 ─────────────────────────── #

# 策略类名到 emoji 的映射
STRATEGY_EMOJIS = {
    "BBIKDJSelector": "🔥",
    "SuperB1Selector": "⚡",
    "PeakKDJSelector": "🎫",
    "BBIShortLongSelector": "🕳️",
    "MA60CrossVolumeWaveSelector": "📈",
    "BigBullishVolumeSelector": "💪",
}

# 类名到类的映射
SELECTOR_CLASSES = {
    "BBIKDJSelector": BBIKDJSelector,
    "SuperB1Selector": SuperB1Selector,
    "PeakKDJSelector": PeakKDJSelector,
    "BBIShortLongSelector": BBIShortLongSelector,
    "MA60CrossVolumeWaveSelector": MA60CrossVolumeWaveSelector,
    "BigBullishVolumeSelector": BigBullishVolumeSelector,
}


def load_config(cfg_path: Path) -> List[Dict[str, Any]]:
    """从 configs.json 加载策略配置"""
    if not cfg_path.exists():
        console.print(f"[red]❌ 配置文件 {cfg_path} 不存在[/red]")
        sys.exit(1)
    with cfg_path.open(encoding="utf-8") as f:
        cfg_raw = json.load(f)

    # 兼容三种结构：单对象、对象数组、或带 selectors 键
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
    """动态加载 Selector 类并实例化"""
    cls_name: str = cfg.get("class")
    if not cls_name:
        raise ValueError("缺少 class 字段")

    cls = SELECTOR_CLASSES.get(cls_name)
    if cls is None:
        raise ImportError(f"未知的 Selector 类: {cls_name}")

    params = cfg.get("params", {})
    alias = cfg.get("alias", cls_name)
    emoji = STRATEGY_EMOJIS.get(cls_name, "📊")
    return alias, cls(**params), emoji


def load_strategies_from_config(cfg_path: Path) -> Dict[str, Dict[str, Any]]:
    """从配置文件加载策略，返回与 STRATEGIES 相同格式的字典"""
    cfgs = load_config(cfg_path)
    strategies = {}
    for cfg in cfgs:
        if cfg.get("activate", True) is False:
            continue
        try:
            alias, selector, emoji = instantiate_selector(cfg)
            strategies[alias] = {
                "selector": selector,
                "emoji": emoji,
            }
        except Exception as e:
            console.print(f"[yellow]⚠️  跳过配置 {cfg}: {e}[/yellow]")
    return strategies


# ─────────────────────────── 数据加载 ─────────────────────────── #

def load_data_polars(data_dir: str, tickers: Optional[List[str]] = None) -> Dict[str, pl.DataFrame]:
    """使用 Polars 直接读取数据（支持 CSV 和 Parquet）"""
    data = {}
    data_path = Path(data_dir)
    
    # 检测数据格式
    parquet_files = list(data_path.glob("*.parquet"))
    csv_files = list(data_path.glob("*.csv"))
    is_parquet = len(parquet_files) > 0
    
    if tickers:
        if is_parquet:
            files = [data_path / f"{t}.parquet" for t in tickers if (data_path / f"{t}.parquet").exists()]
        else:
            files = [data_path / f"{t}.csv" for t in tickers if (data_path / f"{t}.csv").exists()]
    else:
        files = parquet_files if is_parquet else csv_files
    
    for f in files:
        code = f.stem
        try:
            if is_parquet:
                df = pl.read_parquet(f)
            else:
                df = pl.read_csv(
                    f,
                    try_parse_dates=True,
                    infer_schema_length=1000,
                )
            # 确保列名标准化
            df = df.rename({col: col.lower() for col in df.columns})
            # 确保 date 列是日期类型
            if df["date"].dtype == pl.Utf8:
                df = df.with_columns(pl.col("date").str.to_date())
            data[code] = df
        except Exception as e:
            console.print(f"[yellow]⚠️  读取 {f.name} 失败: {e}[/yellow]")
    
    return data


# ─────────────────────────── 结果输出 ─────────────────────────── #

def print_strategy_result(
    strategy_name: str,
    emoji: str,
    picks: List[str],
    elapsed: float,
    date: str,
) -> None:
    """打印单个策略的选股结果"""
    # 创建结果表格
    table = Table(
        show_header=True,
        header_style="bold cyan",
        border_style="bright_blue",
        title=f"{emoji} {strategy_name} 选股结果",
        title_style="bold magenta",
        show_lines=True,
    )
    table.add_column("序号", justify="center", style="bold", width=6)
    table.add_column("股票代码", justify="center", style="bold cyan", width=12)
    table.add_column("状态", justify="center", width=6)
    
    if picks:
        for i, code in enumerate(picks, 1):
            table.add_row(str(i), code, "✅")
    else:
        table.add_row("-", "无符合条件的股票", "-")
    
    console.print(table)
    
    # 打印统计信息
    stats_panel = Panel(
        f"[bold]策略名称:[/bold] {strategy_name}\n"
        f"[bold]交易日期:[/bold] {date}\n"
        f"[bold]选中数量:[/bold] {len(picks)} 只\n"
        f"[bold]耗时:[/bold] {elapsed:.3f} 秒",
        title="📈 选股统计",
        border_style="green",
        expand=False,
    )
    console.print(stats_panel)
    console.print()


def save_results(results: Dict, date: str, output_dir: str) -> str:
    """保存结果到 JSON 文件"""
    os.makedirs(output_dir, exist_ok=True)
    date_str = date.replace("-", "")
    filepath = os.path.join(output_dir, f"{date_str}.json")
    
    # 读取已有结果（如果有）
    existing = {}
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass
    
    # 合并结果
    existing.update(results)
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    
    return filepath


# ─────────────────────────── 主程序 ─────────────────────────── #

def main():
    parser = argparse.ArgumentParser(description="选股程序 - Polars 版本")
    parser.add_argument("--date", required=True, help="选股日期 (YYYY-MM-DD)")
    parser.add_argument("--data-dir", default="data", help="数据目录")
    parser.add_argument("--config", default="configs.json", help="Selector 配置文件")
    parser.add_argument("--output-dir", default="backtest_results", help="结果输出目录")
    parser.add_argument("--tickers", nargs="+", help="指定股票代码")
    parser.add_argument("--strategies", nargs="+", help="指定策略名称")
    
    args = parser.parse_args()
    
    # 验证日期格式
    try:
        date_obj = datetime.strptime(args.date, "%Y-%m-%d").date()
    except ValueError:
        console.print("[red]❌ 日期格式错误，请使用 YYYY-MM-DD 格式[/red]")
        sys.exit(1)
    
    # 打印标题
    console.print()
    console.print(
        Panel(
            f"[bold cyan]选股日期:[/bold cyan] {args.date}\n"
            f"[bold cyan]数据目录:[/bold cyan] {args.data_dir}\n"
            f"[bold cyan]配置文件:[/bold cyan] {args.config}\n"
            f"[bold cyan]结果目录:[/bold cyan] {args.output_dir}",
            title="🚀 选股程序 (Polars 版本)",
            border_style="bright_blue",
            expand=False,
        )
    )
    console.print()
    
    # 加载数据
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("📊 加载数据...", total=None)
        start_time = time.time()
        data = load_data_polars(args.data_dir, args.tickers)
        load_time = time.time() - start_time
        progress.update(task, completed=True)
    
    console.print(f"[green]✅ 成功加载 {len(data)} 只股票的数据 (耗时: {load_time:.2f} 秒)[/green]")
    console.print()
    
    # 从配置文件加载策略
    strategies = load_strategies_from_config(Path(args.config))
    
    # 确定要运行的策略
    if args.strategies:
        strategies_to_run = {k: v for k, v in strategies.items() if k in args.strategies}
    else:
        strategies_to_run = strategies
    
    if not strategies_to_run:
        console.print("[red]❌ 没有可运行的策略[/red]")
        sys.exit(1)
    
    # 运行选股
    all_results = {}
    total_start = time.time()
    
    for strategy_name, config in strategies_to_run.items():
        emoji = config["emoji"]
        selector = config["selector"]
        
        console.print(f"[bold]正在运行: {emoji} {strategy_name}...[/bold]")
        
        start = time.time()
        picks = selector.select(date_obj, data)
        elapsed = time.time() - start
        
        # 打印结果
        print_strategy_result(strategy_name, emoji, picks, elapsed, args.date)
        
        # 保存结果
        all_results[strategy_name] = {
            "date": args.date,
            "stocks": picks,
            "count": len(picks),
        }
    
    total_elapsed = time.time() - total_start
    
    # 保存所有结果
    filepath = save_results(all_results, args.date, args.output_dir)
    console.print(f"[green]💾 结果已保存到: {filepath}[/green]")
    console.print()
    
    # 打印总结
    console.print(
        Panel(
            f"[bold green]✅ 选股完成[/bold green]\n"
            f"[bold]总耗时:[/bold] {total_elapsed:.2f} 秒",
            border_style="green",
            expand=False,
        )
    )


if __name__ == "__main__":
    main()