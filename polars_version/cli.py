#!/usr/bin/env python3
"""
Polars 选股交互式 CLI（菜单版）

特性：
- 方向键上下选择功能
- 支持多选策略（空格勾选）
- 可直接触发：拉取数据 / 最新日选股 / 月度批量 / 单日选股
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import List

from prompt_toolkit.styles import Style
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich import box

try:
    import questionary
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "缺少依赖 questionary，请先执行: pip install questionary"
    ) from exc


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUN_SCRIPT = PROJECT_ROOT / "polars_version" / "run.py"
SELECT_SCRIPT = PROJECT_ROOT / "polars_version" / "select_stock.py"
FETCH_SCRIPT = PROJECT_ROOT / "polars_version" / "fetch_kline.py"
CONFIG_PATH = PROJECT_ROOT / "configs.json"
console = Console()
Q_STYLE = Style.from_dict(
    {
        "qmark": "fg:#d8b4fe bold",
        "question": "fg:#f5f3ff bold",
        "answer": "fg:#e9d5ff bold",
        "pointer": "fg:#c084fc bold",
        "highlighted": "fg:#e9d5ff bold",
        "selected": "fg:#e9d5ff",
        "separator": "fg:#a78bfa",
        "instruction": "fg:#c4b5fd",
        "text": "fg:#f5f3ff",
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
    """交互式选择策略：默认全部，可切换为指定策略。"""
    strategies = _load_active_strategies()
    if not strategies:
        console.print("[yellow]⚠️ 未读取到策略配置，将按全部策略执行。[/yellow]")
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
    console.print(f"[dim]执行命令: {' '.join(cmd)}[/dim]")
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=False)


def _print_run_preview(title: str, lines: List[str]) -> None:
    content = "\n".join(f"- {line}" for line in lines)
    console.print(
        Panel(
            content,
            title=f"[bold #d8b4fe]{title}[/bold #d8b4fe]",
            border_style="#c4b5fd",
        )
    )


def _render_banner() -> None:
    """渲染 CLI 首页 Banner。"""
    banner_lines = Text.assemble(
        "\n",
        "          ",
        ("▲", "bold red"),
        ("  日线观势  ", "bold #f5f3ff"),
        ("▲", "bold red"),
        "\n\n",
        "        量化策略  ·  A股日线分析\n",
        "        自动筛选  ·  趋势研判\n",
        "\n",
    )
    console.print()
    console.print(
        Panel.fit(
            banner_lines,
            border_style="#c4b5fd",
            padding=(0, 2),
        )
    )
    console.print(Text("A股日线级别高性能量化选股程序", style="bold #e9d5ff"), justify="center")
    console.print(Text("author by kangdi.liu", style="bold #d8b4fe"), justify="center")


def _render_feature_table() -> None:
    feature_table = Table(
        show_header=False,
        box=None,
        padding=(0, 1),
        expand=True,
    )
    feature_table.add_column(style="bold #d8b4fe", width=12)
    feature_table.add_column(style="white")
    feature_table.add_row("功能", "最新一键选股 / 月度批量 / 单日选股 / 数据拉取")
    feature_table.add_row("策略选择", "默认全部策略；可切换到“指定策略（多选）”")
    feature_table.add_row("快捷键", "↑↓ 移动 · 空格 勾选 · Enter 确认 · a 全选/取消全选")
    feature_table.add_row("结果目录", "results/polars（选股结果）")
    feature_table.add_row("日志文件", "fetch.log / run.log（运行日志）")
    console.print(
        Panel(
            feature_table,
            border_style="#c4b5fd",
            title="[bold #f5f3ff]使用说明[/bold #f5f3ff]",
            padding=(0, 1),
        )
    )


def _render_action_board() -> None:
    """渲染功能选择面板（比纯文本列表更直观）。"""
    menu_table = Table(
        show_header=True,
        header_style="bold #f5f3ff",
        expand=True,
        box=box.ROUNDED,
        border_style="#c4b5fd",
    )
    menu_table.add_column("功能", style="bold #d8b4fe", width=24)
    menu_table.add_column("说明", style="white")
    menu_table.add_column("建议场景", style="bold #c4b5fd", width=18)
    menu_table.add_row("✨ 最新交易日一键选股", "面向日常使用，快速跑最新交易日策略", "盘后日常")
    menu_table.add_row("📅 月份批量选股", "输入 YYYYMM 或 YYYYMM-YYYYMM，按交易日批量执行", "历史回放")
    menu_table.add_row("🧪 指定日期单日选股", "用于调试某个交易日的策略结果", "策略调试")
    menu_table.add_row("📥 仅拉取行情数据", "仅更新 Parquet 数据，不执行选股", "数据维护")
    menu_table.add_row("🚪 退出", "离开交互终端", "结束会话")
    console.print(
        Panel(
            menu_table,
            border_style="#c4b5fd",
            title="[bold #f5f3ff]功能面板[/bold #f5f3ff]",
            subtitle="[bold #d8b4fe]↑↓ 选择 · Enter 执行 · Space 勾选 · a 全选/反选[/bold #d8b4fe]",
            padding=(0, 1),
        )
    )


def _handle_latest_selection() -> None:
    """最新交易日一键选股。"""
    skip_fetch = questionary.confirm("是否跳过数据拉取？", default=True, style=Q_STYLE).ask()
    selected_strategies = _pick_strategies()
    strategy_desc = "全部策略" if not selected_strategies else f"指定策略 ({', '.join(selected_strategies)})"

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
    strategy_desc = "全部策略" if not selected_strategies else f"指定策略 ({', '.join(selected_strategies)})"

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
    strategy_desc = "全部策略" if not selected_strategies else f"指定策略 ({', '.join(selected_strategies)})"
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
                "最新交易日一键选股",
                "月份批量选股",
                "指定日期单日选股",
                "仅拉取行情数据",
                "退出",
            ],
            style=Q_STYLE,
        ).ask()

        if action == "最新交易日一键选股":
            _handle_latest_selection()
        elif action == "月份批量选股":
            _handle_month_batch()
        elif action == "指定日期单日选股":
            _handle_single_day()
        elif action == "仅拉取行情数据":
            _handle_fetch_only()
        else:
            console.print("[green]👋 已退出交互式 CLI[/green]")
            return

        if not questionary.confirm("是否继续使用菜单？", default=True, style=Q_STYLE).ask():
            console.print("[green]👋 已退出交互式 CLI[/green]")
            return


if __name__ == "__main__":
    main()
