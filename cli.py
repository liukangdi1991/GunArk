#!/usr/bin/env python3
"""Polars 选股交互式 CLI（Codex 风格菜单版）。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import List

from prompt_toolkit.styles import Style
from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

try:
    import questionary
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "缺少依赖 questionary，请先执行: pip install questionary"
    ) from exc


PROJECT_ROOT = Path(__file__).resolve().parent
RUN_SCRIPT = PROJECT_ROOT / "run.py"
SELECT_SCRIPT = PROJECT_ROOT / "select_stock.py"
FETCH_SCRIPT = PROJECT_ROOT / "fetch_kline.py"
CONFIG_PATH = PROJECT_ROOT / "configs.json"
console = Console()
Q_STYLE = Style.from_dict(
    {
        "qmark": "fg:#7dd3fc bold",
        "question": "fg:#dbeafe bold",
        "answer": "fg:#86efac bold",
        "pointer": "fg:#c4b5fd bold",
        "highlighted": "fg:#fef08a bold",
        "selected": "fg:#86efac",
        "separator": "fg:#94a3b8",
        "instruction": "fg:#a5b4fc",
        "text": "fg:#e2e8f0",
    }
)


def _load_active_strategies() -> List[str]:
    """从 configs.json 读取启用中的策略别名。"""
    if not CONFIG_PATH.exists():
        return []
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []

    selectors = payload.get("selectors", []) if isinstance(payload, dict) else []
    strategies: List[str] = []
    for selector in selectors:
        if not isinstance(selector, dict):
            continue
        if selector.get("activate", True) is False:
            continue
        alias = str(selector.get("alias", "")).strip()
        if alias:
            strategies.append(alias)
    return strategies


def _pick_strategies() -> List[str]:
    """交互式选择策略：默认按配置，可切换为指定策略。"""
    strategies = _load_active_strategies()
    if not strategies:
        console.print("[yellow][WARN][/yellow] 未读取到策略配置，将回退到程序默认策略。")
        return []

    mode = questionary.select(
        "策略执行方式",
        choices=[
            "全部策略（默认）",
            "指定策略（多选）",
        ],
        default="全部策略（默认）",
        style=Q_STYLE,
    ).ask()
    if mode != "指定策略（多选）":
        return []

    selected = questionary.checkbox(
        "选择要运行的策略（空格勾选，回车确认）",
        choices=strategies,
        style=Q_STYLE,
    ).ask() or []
    return selected


def _ask_month_input() -> str:
    return questionary.text(
        "请输入月份（YYYYMM）或区间（YYYYMM-YYYYMM）",
        validate=lambda t: bool(re.fullmatch(r"\d{6}(-\d{6})?", t.strip())),
        style=Q_STYLE,
    ).ask().strip()


def _ask_date_input() -> str:
    return questionary.text(
        "请输入选股日期（YYYY-MM-DD）",
        validate=lambda t: bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", t.strip())),
        style=Q_STYLE,
    ).ask().strip()


def _run_command(args: List[str]) -> None:
    cmd = [sys.executable, *args]
    console.print(f"[bold #22d3ee][CMD][/bold #22d3ee] [#bfdbfe]{' '.join(cmd)}[/#bfdbfe]")
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=False)


def _print_run_preview(title: str, lines: List[str]) -> None:
    content = "\n".join(f"- {line}" for line in lines)
    console.print(
        Panel(
            content,
            title=f"[bold #a5b4fc]{title}[/bold #a5b4fc]",
            border_style="#818cf8",
            box=box.SQUARE,
        )
    )


def _render_banner() -> None:
    """渲染 CLI 首页 Banner（Codex 风格）。"""
    title_text = Text("A股日线高性能量化选股程序", style="bold #7dd3fc")
    author_text = Text("author: kangdi.liu", style="bold #86efac")
    console.print()
    console.print(
        Panel(
            Align.center(
                Text("\n").append_text(title_text).append("\n").append_text(author_text).append("\n")
            ),
            title="[bold #f9a8d4]日线观势[/bold #f9a8d4]",
            border_style="#38bdf8",
            box=box.SQUARE,
            padding=(1, 2),
        )
    )


def _render_feature_table() -> None:
    feature_table = Table(
        show_header=False,
        box=box.SIMPLE,
        padding=(0, 1),
        expand=True,
    )
    feature_table.add_column(style="bold #67e8f9", width=14)
    feature_table.add_column(style="#e2e8f0")
    feature_table.add_row("功能", "最新一键选股 / 月度批量 / 单日选股 / 数据拉取")
    feature_table.add_row("策略选择", "默认按配置 default_strategies；可切换为“指定策略（多选）”")
    feature_table.add_row("快捷键", "↑↓ 移动 · 空格 勾选 · Enter 确认 · a 全选/取消全选")
    feature_table.add_row("结果目录", "results/signals（选股结果）")
    feature_table.add_row("日志文件", "fetch.log / run.log（运行日志）")
    console.print(
        Panel(
            feature_table,
            border_style="#22c55e",
            title="[bold #86efac]使用说明[/bold #86efac]",
            box=box.SQUARE,
            padding=(0, 1),
        )
    )


def _render_action_board() -> None:
    """渲染功能选择面板。"""
    menu_table = Table(
        show_header=True,
        header_style="bold #fde68a",
        expand=True,
        box=box.SIMPLE_HEAVY,
        border_style="#22d3ee",
    )
    menu_table.add_column("功能", style="bold #93c5fd", width=24)
    menu_table.add_column("说明", style="#e2e8f0")
    menu_table.add_column("建议场景", style="bold #86efac", width=18)
    menu_table.add_row("✨ 最新交易日一键选股", "面向日常使用，快速跑最新交易日策略", "盘后日常")
    menu_table.add_row("📅 月份批量选股", "输入 YYYYMM 或 YYYYMM-YYYYMM，按交易日批量执行", "历史回放")
    menu_table.add_row("🧪 指定日期单日选股", "用于调试某个交易日的策略结果", "策略调试")
    menu_table.add_row("📥 仅拉取行情数据", "仅更新 Parquet 数据，不执行选股", "数据维护")
    menu_table.add_row("🚪 退出", "离开交互终端", "结束会话")
    console.print(
        Panel(
            menu_table,
            border_style="#14b8a6",
            title="[bold #5eead4]支持的命令[/bold #5eead4]",
            subtitle="[bold #c4b5fd]↑↓ 选择 · Enter 执行 · Space 勾选 · a 全选/反选[/bold #c4b5fd]",
            box=box.SQUARE,
            padding=(0, 1),
        )
    )


def _handle_latest_selection() -> None:
    """最新交易日一键选股。"""
    skip_fetch = questionary.confirm("是否跳过数据拉取？", default=True, style=Q_STYLE).ask()
    selected_strategies = _pick_strategies()
    strategy_desc = "默认策略（配置）" if not selected_strategies else f"指定策略 ({', '.join(selected_strategies)})"

    args = [str(RUN_SCRIPT)]
    if skip_fetch:
        args.append("--skip-fetch")
    if selected_strategies:
        args.extend(["--strategies", *selected_strategies])
    _print_run_preview(
        "执行预览｜最新交易日一键选股",
        [
            f"数据拉取: {'跳过' if skip_fetch else '执行'}",
            f"策略范围: {strategy_desc}",
        ],
    )
    _run_command(args)


def _handle_month_batch() -> None:
    """按月份批量选股。"""
    month = _ask_month_input()
    skip_fetch = questionary.confirm("是否跳过数据拉取？", default=True, style=Q_STYLE).ask()
    selected_strategies = _pick_strategies()
    strategy_desc = "默认策略（配置）" if not selected_strategies else f"指定策略 ({', '.join(selected_strategies)})"

    args = [str(RUN_SCRIPT), month]
    if skip_fetch:
        args.append("--skip-fetch")
    if selected_strategies:
        args.extend(["--strategies", *selected_strategies])
    _print_run_preview(
        "执行预览｜月份批量选股",
        [
            f"月份参数: {month}",
            f"数据拉取: {'跳过' if skip_fetch else '执行'}",
            f"策略范围: {strategy_desc}",
        ],
    )
    _run_command(args)


def _handle_single_day() -> None:
    """指定某日执行单日选股。"""
    date_str = _ask_date_input()
    selected_strategies = _pick_strategies()
    strategy_desc = "默认策略（配置）" if not selected_strategies else f"指定策略 ({', '.join(selected_strategies)})"
    args = [str(SELECT_SCRIPT), "--date", date_str]
    if selected_strategies:
        args.extend(["--strategies", *selected_strategies])
    _print_run_preview(
        "执行预览｜指定日期单日选股",
        [
            f"选股日期: {date_str}",
            f"策略范围: {strategy_desc}",
        ],
    )
    _run_command(args)


def _handle_fetch_only() -> None:
    """仅拉取行情数据。"""
    start = questionary.text("起始日期（YYYYMMDD 或 today）", default="20190101", style=Q_STYLE).ask().strip()
    end = questionary.text("结束日期（YYYYMMDD 或 today）", default="today", style=Q_STYLE).ask().strip()
    boards = questionary.checkbox(
        "可选：排除板块（空格勾选）",
        choices=[
            {"name": "创业板 gem", "value": "gem"},
            {"name": "科创板 star", "value": "star"},
            {"name": "北交所 bj", "value": "bj"},
        ],
        style=Q_STYLE,
    ).ask() or []

    args = [str(FETCH_SCRIPT), "--start", start, "--end", end]
    if boards:
        args.extend(["--exclude-boards", *boards])
    _print_run_preview(
        "执行预览｜仅拉取行情数据",
        [
            f"日期范围: {start} ~ {end}",
            f"排除板块: {', '.join(boards) if boards else '无'}",
        ],
    )
    _run_command(args)


def main() -> None:
    console.clear()
    _render_banner()
    _render_feature_table()

    while True:
        _render_action_board()
        action = questionary.select(
            "请选择功能",
            choices=[
                "✨ 最新交易日一键选股",
                "📅 月份批量选股",
                "🧪 指定日期单日选股",
                "📥 仅拉取行情数据",
                "🚪 退出",
            ],
            style=Q_STYLE,
        ).ask()

        if action == "✨ 最新交易日一键选股":
            _handle_latest_selection()
        elif action == "📅 月份批量选股":
            _handle_month_batch()
        elif action == "🧪 指定日期单日选股":
            _handle_single_day()
        elif action == "📥 仅拉取行情数据":
            _handle_fetch_only()
        else:
            console.print("[bold #4ade80][DONE][/bold #4ade80] [#bbf7d0]已退出交互式 CLI[/#bbf7d0]")
            return

        if not questionary.confirm("是否继续使用菜单？", default=True, style=Q_STYLE).ask():
            console.print("[bold #4ade80][DONE][/bold #4ade80] [#bbf7d0]已退出交互式 CLI[/#bbf7d0]")
            return


if __name__ == "__main__":
    main()
