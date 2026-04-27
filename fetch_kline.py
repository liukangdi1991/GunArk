#!/usr/bin/env python3
"""
数据拉取程序 (Polars 版)
- 从 Tushare 拉取日线K线数据
- 直接保存为 Parquet 格式到 db/ 目录
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import random
import sys
import time
import warnings
from pathlib import Path
from typing import List, Optional

import pandas as pd
import polars as pl
import tushare as ts
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich import box

warnings.filterwarnings("ignore")

console = Console()

PROJECT_ROOT = Path(__file__).resolve().parent

LOG_FILE = PROJECT_ROOT / "fetch.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger("fetch_market_data")

COOLDOWN_SECS = 600
BAN_PATTERNS = (
    "访问频繁", "请稍后", "超过频率", "频繁访问",
    "too many requests", "429",
    "forbidden", "403",
    "max retries exceeded"
)


def _looks_like_ip_ban(exc: Exception) -> bool:
    msg = (str(exc) or "").lower()
    return any(pat in msg for pat in BAN_PATTERNS)


class RateLimitError(RuntimeError):
    pass


def _cool_sleep(base_seconds: int) -> None:
    jitter = random.uniform(0.9, 1.2)
    sleep_s = max(1, int(base_seconds * jitter))
    console.print(f"[yellow]⚠️  疑似被限流/封禁，进入冷却期 {sleep_s} 秒...[/yellow]")
    time.sleep(sleep_s)


pro: Optional[ts.pro_api] = None


def _resolve_date_arg(value: str) -> str:
    """将命令行日期参数标准化为 YYYYMMDD。"""
    return dt.date.today().strftime("%Y%m%d") if str(value).lower() == "today" else value


def _setup_network_env() -> None:
    """统一设置 Tushare 访问相关环境变量。"""
    os.environ["NO_PROXY"] = "api.waditu.com,.waditu.com,waditu.com"
    os.environ["no_proxy"] = os.environ["NO_PROXY"]


def _setup_tushare_client() -> None:
    """初始化 Tushare 客户端并写入全局 pro。"""
    ts_token = os.environ.get("TUSHARE_TOKEN")
    if not ts_token:
        # 兼容旧行为：环境变量未设置时使用默认 token
        ts_token = "8a835a0cbcf32855a41cfe05457833bfd081de082a2699db11a2c484"
    ts.set_token(ts_token)
    global pro
    pro = ts.pro_api()


def _to_ts_code(code: str) -> str:
    """将 6 位股票代码转换为 Tushare ts_code。"""
    code = str(code).zfill(6)
    if code.startswith(("60", "68", "9")):
        return f"{code}.SH"
    elif code.startswith(("4", "8")):
        return f"{code}.BJ"
    else:
        return f"{code}.SZ"


def _get_kline_tushare(code: str, start: str, end: str) -> pd.DataFrame:
    """从 Tushare 拉取单只股票日线数据。"""
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
            raise RateLimitError(str(e)) from e
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
    """抓取并落盘单只股票日线数据（最多重试 3 次）。"""
    parquet_path = out_dir / f"{code}.parquet"

    for attempt in range(1, 4):
        try:
            pdf = _get_kline_tushare(code, start, end)
            if pdf.empty:
                return
            pdf = validate(pdf)
            pdf["date"] = pdf["date"].dt.strftime("%Y-%m-%d")
            pl_df = pl.from_pandas(pdf).with_columns(
                pl.col("date").str.strptime(pl.Date, "%Y-%m-%d")
            ).sort("date")
            pl_df.write_parquet(parquet_path, compression="zstd")
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


def main():
    parser = argparse.ArgumentParser(description="从 Tushare 抓取日线K线并保存为 Parquet（Polars 版）")
    parser.add_argument("--start", default="20190101", help="起始日期 YYYYMMDD 或 'today'")
    parser.add_argument("--end", default="today", help="结束日期 YYYYMMDD 或 'today'")
    parser.add_argument("--stocklist", type=Path, default=PROJECT_ROOT / "stocklist.csv", help="股票清单CSV路径")
    parser.add_argument(
        "--exclude-boards",
        nargs="*",
        default=[],
        choices=["gem", "star", "bj"],
        help="排除板块：gem(创业板) star(科创板) bj(北交所)"
    )
    parser.add_argument("--out", default=str(PROJECT_ROOT / "db"), help="Parquet 输出目录")
    args = parser.parse_args()

    _setup_network_env()
    _setup_tushare_client()

    start = _resolve_date_arg(args.start)
    end = _resolve_date_arg(args.end)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    exclude_boards = set(args.exclude_boards or [])
    codes = load_codes_from_stocklist(args.stocklist, exclude_boards)

    if not codes:
        logger.error("stocklist 为空或被过滤后无代码，请检查。")
        sys.exit(1)

    info_panel = Panel(
        f"[bold cyan]📥 开始拉取数据 (Polars 版)[/bold cyan]\n\n"
        f"  股票数量: [bold green]{len(codes)}[/bold green]\n"
        f"  数据源: [bold]Tushare (日线, qfq)[/bold]\n"
        f"  日期范围: [bold]{start}[/bold] → [bold]{end}[/bold]\n"
        f"  输出格式: [bold]Parquet (zstd)[/bold]\n"
        f"  排除板块: [dim]{', '.join(sorted(exclude_boards)) or '无'}[/dim]\n"
        f"  保存目录: [dim]{out_dir.resolve()}[/dim]",
        title="[bold blue]数据拉取[/bold blue]",
        border_style="blue",
        box=box.ROUNDED
    )
    console.print(info_panel)
    console.print()

    t0 = time.time()
    total = len(codes)
    # Textual 日志面板通过子进程管道采集输出，rich live progress 在非 TTY 下不易呈现。
    # 因此在非 TTY 场景退化为普通文本进度行，确保日志区可见。
    if sys.stdout.isatty():
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=40),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]下载中...", total=total)
            for code in codes:
                fetch_one(code, start, end, out_dir)
                progress.advance(task)
    else:
        for idx, code in enumerate(codes, start=1):
            fetch_one(code, start, end, out_dir)
            if idx == 1 or idx == total or idx % 20 == 0:
                pct = idx * 100.0 / total
                console.print(f"[cyan]下载进度: {idx}/{total} ({pct:.1f}%)[/cyan]")

    elapsed = time.time() - t0
    console.print()
    console.print(f"[green]✅ 全部任务完成 ({elapsed:.1f}s)，数据已保存至 {out_dir.resolve()}[/green]")


if __name__ == "__main__":
    main()
